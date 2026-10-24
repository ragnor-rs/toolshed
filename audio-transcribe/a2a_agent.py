#!/usr/bin/env python3
"""
A2A agent for meeting audio transcription and summarization.

Demonstrates three A2A protocol concepts:
  1. Tool use — calls transcribe_gemini.py as an external tool
  2. Decision making — agent decides whether validation is needed
     based on audio duration and effort parameter
  3. Long-running operations — SSE streaming, input-required states,
     multi-step task lifecycle

Two skills:
  - transcribe: audio → diarized transcript (+ optional speaker validation)
  - summarize:  transcript(s) → structured summary (+ optional fact-checking)

Usage:
    python3 a2a_agent.py --port 5001 --gemini-key AIza...
"""

import argparse
import asyncio
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

# ---------------------------------------------------------------------------
# SSL
# ---------------------------------------------------------------------------
try:
    import certifi
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_ctx = ssl.create_default_context()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".webm"}
GEMINI_MODEL = "gemini-2.5-pro"
MAX_RETRIES = 5

SCRIPT_DIR = Path(__file__).parent
TRANSCRIBE_SCRIPT = SCRIPT_DIR / "scripts" / "transcribe_gemini.py"
if not TRANSCRIBE_SCRIPT.exists():
    TRANSCRIBE_SCRIPT = SCRIPT_DIR / "transcribe_gemini.py"


def load_env_file() -> None:
    """Populate os.environ from the nearest .env, searching up from cwd then here.

    Duplicated from transcribe_gemini.py rather than shared: both files are meant to
    stay droppable into another project on their own. A real environment variable
    always wins over the file.
    """
    for start in (Path.cwd(), SCRIPT_DIR):
        for d in [start.resolve(), *start.resolve().parents]:
            candidate = d / ".env"
            if not candidate.is_file():
                continue
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].lstrip()
                    key, sep, value = line.partition("=")
                    if not sep:
                        continue
                    key, value = key.strip(), value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = value
            except OSError:
                pass
            return

# ---------------------------------------------------------------------------
# Gemini text API (zero-dep)
# ---------------------------------------------------------------------------

def gemini_generate(api_key: str, system: str, user_text: str) -> str:
    """Sync Gemini 2.5 Pro call with retries. Zero deps — raw urllib."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 65000},
    }
    data = json.dumps(payload).encode()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600, context=_ssl_ctx) as resp:
                result = json.loads(resp.read())
            if "error" in result:
                raise RuntimeError(result["error"]["message"])
            parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = parts[0]["text"] if parts else ""
            if not text.strip():
                raise RuntimeError("Empty response")
            return text
        except Exception as e:
            if any(c in str(e) for c in ["503", "429", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "Empty"]) and attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise


async def gemini_async(api_key: str, system: str, user_text: str) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, gemini_generate, api_key, system, user_text)


# ---------------------------------------------------------------------------
# Task context
# ---------------------------------------------------------------------------

@dataclass
class TaskContext:
    task_id: str
    session_id: str
    state: str = "submitted"
    history: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    subscribers: list = field(default_factory=list)       # SSE queues
    input_event: asyncio.Event = field(default_factory=asyncio.Event)
    user_input: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    pipeline_task: asyncio.Task | None = None
    # Skill-specific state
    api_key: str = ""
    workdir: str = "."
    effort: str = "normal"     # low | normal | high — controls validation depth
    metadata: dict = field(default_factory=dict)

tasks: dict[str, TaskContext] = {}
_gemini_key: str = ""

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------

AGENT_CARD = {
    "name": "Meeting Transcription Agent",
    "description": (
        "Transcribes meeting audio with speaker diarization and generates "
        "validated summaries. Uses Gemini 2.5 Pro. Agent autonomously decides "
        "validation depth based on input characteristics and effort parameter."
    ),
    "url": "http://localhost:5001",
    "version": "1.0.0",
    "protocolVersion": "0.2.0",
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    },
    "defaultInputModes": ["text/plain", "audio/mp4"],
    "defaultOutputModes": ["text/plain"],
    "skills": [
        {
            "id": "transcribe",
            "name": "Transcribe Audio",
            "description": (
                "Transcribe audio file(s) with speaker diarization. "
                "TOOL: calls transcribe_gemini.py. "
                "DECISION: agent decides whether to validate speaker attributions "
                "based on audio duration and effort parameter. "
                "LONG-RUNNING: streams progress, may pause for human validation."
            ),
            "tags": ["transcription", "audio", "diarization", "meeting"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        },
        {
            "id": "summarize",
            "name": "Summarize Transcripts",
            "description": (
                "Generate structured meeting summary from transcripts. "
                "TOOL: calls Gemini 2.5 Pro for generation and fact-checking. "
                "DECISION: agent decides whether to fact-check the summary "
                "based on transcript length and effort parameter. "
                "LONG-RUNNING: summary generation can take minutes for long meetings."
            ),
            "tags": ["summary", "analysis", "meeting-notes"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        },
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def msg(role: str, text: str) -> dict:
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def artifact(name: str, text: str) -> dict:
    return {"name": name, "parts": [{"type": "text", "text": text}]}


def snapshot(ctx: TaskContext) -> dict:
    r = {
        "id": ctx.task_id,
        "sessionId": ctx.session_id,
        "status": {"state": ctx.state},
        "artifacts": ctx.artifacts,
        "history": ctx.history,
    }
    if ctx.history:
        r["status"]["message"] = ctx.history[-1]
    return r


async def emit(ctx: TaskContext, state: str | None = None, text: str | None = None):
    if state:
        ctx.state = state
    if text:
        ctx.history.append(msg("agent", text))
    data = snapshot(ctx)
    for q in ctx.subscribers:
        await q.put(data)


async def wait_input(ctx: TaskContext) -> str:
    """Block until user sends input. Returns the text."""
    ctx.input_event.clear()
    await ctx.input_event.wait()
    return ctx.user_input.strip()


def get_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
            ".ogg": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm"}.get(ext, "audio/mpeg")


def find_audio_files(workdir: str) -> list[str]:
    return sorted(
        os.path.join(workdir, f)
        for f in os.listdir(workdir)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        and os.path.isfile(os.path.join(workdir, f))
        and os.path.getsize(os.path.join(workdir, f)) > 0
    )


def estimate_duration_minutes(transcript: str) -> float:
    """Estimate audio duration from last timestamp in transcript."""
    timestamps = re.findall(r"\[(\d{1,2}):(\d{1,2}):(\d{1,2})\]", transcript)
    if not timestamps:
        return 0
    h, m, s = [int(x) for x in timestamps[-1]]
    return h * 60 + m + s / 60


def select_samples(transcript: str, min_samples: int = 5) -> list[tuple[int, str, str, str]]:
    """Pick random substantial lines (10+ words). Returns (line_no, ts, speaker, text)."""
    entries = []
    for i, line in enumerate(transcript.splitlines()):
        line = line.strip()
        m = re.match(r"\[(\d{1,2}:\d{1,2}:\d{1,2})\]\s+(.+?):\s+(.+)", line)
        if m and len(m.group(3).split()) >= 10:
            entries.append((i + 1, m.group(1), m.group(2), m.group(3)))
    if not entries:
        return []

    duration = estimate_duration_minutes(transcript)
    n = max(min_samples, int(duration / 3))
    n = min(n, len(entries))
    selected = random.sample(entries, n)
    selected.sort(key=lambda x: x[0])
    return selected


def format_samples_table(samples: list, filename: str) -> str:
    lines = [
        f"Speaker validation for **{filename}** ({len(samples)} samples)\n",
        "| # | Time | Speaker | Text |",
        "|---|------|---------|------|",
    ]
    for idx, (lineno, ts, speaker, text) in enumerate(samples, 1):
        preview = text[:100] + ("..." if len(text) > 100 else "")
        lines.append(f"| {idx} | {ts} | **{speaker}** | {preview} |")
    return "\n".join(lines)


def apply_corrections(transcript: str, corrections: str, samples: list) -> str:
    """Apply corrections: '#3 Иван, #7 Мария' or '#3 is actually Иван'."""
    lines = transcript.split("\n")
    for part in corrections.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"#?(\d+)\s*(?:[:=]|is actually|is|это|→|->)\s*(.+)", part, re.IGNORECASE)
        if m:
            idx = int(m.group(1)) - 1
            new_speaker = m.group(2).strip()
            if 0 <= idx < len(samples):
                lineno, ts, old_speaker, _ = samples[idx]
                for i, l in enumerate(lines):
                    if f"[{ts}]" in l and old_speaker in l:
                        lines[i] = re.sub(
                            r"(\[" + re.escape(ts) + r"\])\s+" + re.escape(old_speaker) + r":",
                            rf"\1 {new_speaker}:", l,
                        )
                        break
    return "\n".join(lines)


def parse_request(text: str) -> dict:
    """Parse key=value pairs from request text."""
    result = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            result[k.strip().lower()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# SKILL 1: TRANSCRIBE
#
# Demonstrates:
#   - Tool use: calls transcribe_gemini.py as subprocess
#   - Decision making: decides whether to validate based on duration + effort
#   - Long-running: SSE progress, input-required for validation
# ---------------------------------------------------------------------------

async def skill_transcribe(ctx: TaskContext):
    """Transcribe audio files with optional speaker validation."""

    # --- Discover audio files ---
    await emit(ctx, "working", "Discovering audio files...")
    audio_files = find_audio_files(ctx.workdir)
    if not audio_files:
        await emit(ctx, "failed", "No audio files found in the working directory.")
        return

    names_sizes = ", ".join(
        f"{os.path.basename(f)} ({os.path.getsize(f)/1024/1024:.1f}MB)"
        for f in audio_files
    )
    await emit(ctx, text=f"Found {len(audio_files)} file(s): {names_sizes}")

    # --- Get configuration (input-required) ---
    await emit(ctx, "input-required",
        "Please provide speaker configuration:\n\n"
        "speakers: Name1, Name2, Name3\n"
        "hints: Name1=role, Name2=role\n"
        "language: ru\n"
        "effort: low|normal|high  (controls validation depth, default: normal)"
        + ("" if ctx.api_key else "\napi_key: your-gemini-key")
    )

    user_text = await wait_input(ctx)
    if ctx.cancel_event.is_set():
        return

    config = parse_request(user_text)
    speakers = [s.strip() for s in config.get("speakers", user_text.split("\n")[0]).split(",") if s.strip()]
    hints = {}
    for pair in config.get("hints", config.get("speaker-hints", "")).split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            hints[k.strip()] = v.strip()
    language = config.get("language", "ru")
    effort = config.get("effort", "normal")
    if not ctx.api_key:
        ctx.api_key = config.get("api_key", config.get("api-key", "")) or os.environ.get("GEMINI_API_KEY", "")

    if not speakers:
        await emit(ctx, "failed", "No speakers provided.")
        return
    if not ctx.api_key:
        await emit(ctx, "failed", "No API key available.")
        return

    ctx.effort = effort
    await emit(ctx, "working", f"Configuration: {', '.join(speakers)} | {language} | effort={effort}")

    # --- TOOL USE: call transcribe_gemini.py for each file ---
    script = str(TRANSCRIBE_SCRIPT)
    transcription_files = {}

    for audio_path in audio_files:
        if ctx.cancel_event.is_set():
            return
        fname = os.path.basename(audio_path)
        output_path = os.path.splitext(audio_path)[0] + ".txt"
        hints_str = ",".join(f"{k}={v}" for k, v in hints.items())

        cmd = [
            sys.executable, script,
            "--file", audio_path,
            "--speakers", ",".join(speakers),
            "--language", language,
            "--api-key", ctx.api_key,
            "--output", output_path,
        ]
        if hints_str:
            cmd.extend(["--speaker-hints", hints_str])

        await emit(ctx, text=f"[TOOL] Running transcribe_gemini.py on {fname}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        # Stream progress from tool's stderr. Keep every line — the loop drains the
        # pipe, so there is nothing left to read back if the tool exits non-zero.
        stderr_lines = []
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode(errors="replace").rstrip())
            try:
                evt = json.loads(line.decode().strip())
                etype = evt.get("event", "")
                if etype == "upload_start":
                    await emit(ctx, text=f"[TOOL] Uploading {fname} to Gemini Files API...")
                elif etype == "upload_complete":
                    await emit(ctx, text=f"[TOOL] Upload complete. Starting transcription...")
                elif etype == "transcribe_start":
                    attempt = evt.get("attempt", 1)
                    if attempt > 1:
                        await emit(ctx, text=f"[TOOL] Retry attempt {attempt}...")
                elif etype == "error":
                    await emit(ctx, text=f"[TOOL] Transient error: {evt.get('message', '')} — retrying in {evt.get('wait_seconds', '?')}s")
                elif etype == "transcribe_complete":
                    stats = evt.get("speakers", {})
                    info = ", ".join(f"{k}: {v}" for k, v in stats.items()) if stats else ""
                    await emit(ctx, text=f"[TOOL] {fname} transcribed ({evt.get('lines', '?')} lines). {info}")
            except (json.JSONDecodeError, KeyError):
                pass

        await proc.wait()
        if proc.returncode != 0:
            stderr_text = "\n".join(stderr_lines[-20:]) or "(no output on stderr)"
            await emit(ctx, "failed",
                f"[TOOL] transcribe_gemini.py failed for {fname} "
                f"(exit {proc.returncode}):\n{stderr_text}")
            return

        transcription_files[audio_path] = output_path

    # --- DECISION: should we validate speaker attributions? ---
    for audio_path, txt_path in transcription_files.items():
        fname = os.path.basename(txt_path)
        with open(txt_path, "r", encoding="utf-8") as f:
            transcript = f.read()

        duration = estimate_duration_minutes(transcript)
        speaker_counts = Counter()
        for line in transcript.splitlines():
            m = re.match(r"\[\d+:\d+:\d+\]\s+(.+?):", line)
            if m:
                speaker_counts[m.group(1)] += 1

        # Decision logic
        should_validate = False
        reason = ""

        if effort == "high":
            should_validate = True
            reason = "effort=high → always validate"
        elif effort == "low":
            should_validate = False
            reason = "effort=low → skipping validation"
        else:  # normal
            if duration < 2:
                should_validate = False
                reason = f"audio is short ({duration:.0f}min) → skipping validation"
            elif len(speaker_counts) <= 1:
                should_validate = False
                reason = "only 1 speaker detected → no attribution to validate"
            elif len(speaker_counts) >= 4:
                should_validate = True
                reason = f"{len(speaker_counts)} speakers detected → validation recommended"
            elif duration > 15:
                should_validate = True
                reason = f"long recording ({duration:.0f}min) → validation recommended"
            else:
                should_validate = True
                reason = f"{len(speaker_counts)} speakers, {duration:.0f}min → validation recommended"

        await emit(ctx, text=f"[DECISION] {fname}: {reason}")

        if should_validate:
            # --- Validation loop (input-required) ---
            validation_done = False
            while not validation_done:
                samples = select_samples(transcript)
                if not samples:
                    await emit(ctx, text=f"No substantial utterances to validate in {fname}.")
                    break

                table = format_samples_table(samples, fname)
                await emit(ctx, "input-required",
                    f"{table}\n\n"
                    "Reply **OK** if correct, or specify corrections:\n"
                    "e.g. '#3 Иван, #7 Мария'"
                )

                response = await wait_input(ctx)
                if ctx.cancel_event.is_set():
                    return

                if response.lower() in ("ok", "ок", "yes", "да", "+", "correct", "верно"):
                    await emit(ctx, "working", f"Speaker attribution confirmed for {fname}.")
                    validation_done = True
                else:
                    transcript = apply_corrections(transcript, response, samples)
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(transcript)
                    await emit(ctx, "working", f"Corrections applied to {fname}. Re-sampling...")

    # --- Build artifacts ---
    for audio_path, txt_path in transcription_files.items():
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()
        ctx.artifacts.append(artifact(os.path.basename(txt_path), content))

    await emit(ctx, "completed",
        f"Transcription complete. {len(transcription_files)} file(s) processed.")


# ---------------------------------------------------------------------------
# SKILL 2: SUMMARIZE
#
# Demonstrates:
#   - Tool use: calls Gemini 2.5 Pro for summary generation
#   - Decision making: decides whether to fact-check based on length + effort
#   - Long-running: generation can take minutes for long transcripts
# ---------------------------------------------------------------------------

async def skill_summarize(ctx: TaskContext):
    """Generate structured meeting summary with optional fact-checking."""

    # --- Discover transcript files ---
    await emit(ctx, "working", "Looking for transcript files...")

    txt_files = sorted(
        os.path.join(ctx.workdir, f)
        for f in os.listdir(ctx.workdir)
        if f.endswith(".txt")
        and not f.endswith(".backup.txt")
        and f != "summary.txt"
        and os.path.isfile(os.path.join(ctx.workdir, f))
    )

    if not txt_files:
        await emit(ctx, "failed", "No transcript .txt files found.")
        return

    # Load transcripts
    all_text = []
    total_lines = 0
    for p in txt_files:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        lines = len(content.strip().splitlines())
        total_lines += lines
        all_text.append(f"=== {os.path.basename(p)} ({lines} lines) ===\n{content}")

    combined = "\n\n".join(all_text)
    await emit(ctx, text=f"Found {len(txt_files)} transcript(s), {total_lines} total lines.")

    # Check if we need effort / api key
    if not ctx.api_key:
        await emit(ctx, "input-required",
            "Please provide:\n\n"
            "api_key: your-gemini-key\n"
            "effort: low|normal|high  (controls fact-checking depth, default: normal)"
        )
        response = await wait_input(ctx)
        if ctx.cancel_event.is_set():
            return
        config = parse_request(response)
        ctx.api_key = config.get("api_key", config.get("api-key", response.strip()))
        ctx.effort = config.get("effort", "normal")

    if not ctx.api_key:
        await emit(ctx, "failed", "No API key available.")
        return

    # --- TOOL USE: call Gemini for summary generation ---
    await emit(ctx, "working", "[TOOL] Calling Gemini 2.5 Pro for summary generation... (this may take a few minutes)")

    system = """You are an expert meeting analyst. Generate a structured summary.

Include:
1. Key discussion points — attributed to the speaker who raised each
2. Important quotes — exact quotes where wording matters
3. Decisions made — who proposed, who agreed
4. Action items — who is responsible
5. Open questions — unresolved issues

Rules:
- Every claim attributed to a specific speaker with quote or timestamp
- Do not fabricate anything not in the transcription
- Write in the same language as the transcription
- Use clear section headers"""

    try:
        summary = await gemini_async(ctx.api_key, system, f"Meeting transcription(s):\n\n{combined}")
    except Exception as e:
        await emit(ctx, "failed", f"[TOOL] Summary generation failed: {e}")
        return

    summary_path = os.path.join(ctx.workdir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    await emit(ctx, text=f"[TOOL] Summary generated ({len(summary.splitlines())} lines).")

    # --- DECISION: should we fact-check the summary? ---
    should_validate = False
    reason = ""

    if ctx.effort == "high":
        should_validate = True
        reason = "effort=high → always fact-check"
    elif ctx.effort == "low":
        should_validate = False
        reason = "effort=low → skipping fact-check"
    else:  # normal
        if total_lines < 50:
            should_validate = False
            reason = f"short transcript ({total_lines} lines) → skipping fact-check"
        elif len(txt_files) >= 2:
            should_validate = True
            reason = f"multiple files ({len(txt_files)}) → fact-check recommended"
        elif total_lines > 300:
            should_validate = True
            reason = f"long transcript ({total_lines} lines) → fact-check recommended"
        else:
            should_validate = True
            reason = "standard length → fact-check recommended"

    await emit(ctx, text=f"[DECISION] Fact-check: {reason}")

    if should_validate:
        await emit(ctx, text="[TOOL] Calling Gemini 2.5 Pro for fact-checking...")

        validate_system = """You are a fact-checker for meeting summaries.

For each point in the summary, verify:
1. Speaker attribution correct?
2. Content accurate (no fabrication)?
3. Quotes exact?
4. Anything important missing?

Output:
- If all correct: "VALIDATED: No issues found." then the summary unchanged.
- If issues found: list each issue, then output the full corrected summary."""

        try:
            validation = await gemini_async(
                ctx.api_key, validate_system,
                f"SUMMARY:\n{summary}\n\nSOURCE TRANSCRIPTION:\n{combined}",
            )
        except Exception as e:
            await emit(ctx, text=f"[TOOL] Fact-check failed ({e}), keeping original summary.")
            validation = None

        if validation:
            if "VALIDATED" in validation and "No issues" in validation:
                await emit(ctx, text="[TOOL] Fact-check passed — no issues found.")
            else:
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(validation)
                await emit(ctx, text="[TOOL] Fact-check found issues — summary corrected.")

    # --- Artifact ---
    with open(summary_path, "r", encoding="utf-8") as f:
        final_summary = f.read()
    ctx.artifacts.append(artifact("summary.txt", final_summary))

    await emit(ctx, "completed", f"Summary complete → {summary_path}")


# ---------------------------------------------------------------------------
# Skill router
# ---------------------------------------------------------------------------

def resolve_skill(params: dict) -> str:
    """Return skill ID from params, falling back to keyword detection."""
    skill_id = params.get("skill", "").strip()
    if skill_id in SKILL_MAP:
        return skill_id
    text = extract_text(params.get("message", {})).lower()
    if any(w in text for w in ["summar", "резюме", "краткое", "итог"]):
        return "summarize"
    return "transcribe"


SKILL_MAP = {
    "transcribe": skill_transcribe,
    "summarize": skill_summarize,
}


async def run_skill(ctx: TaskContext, skill_id: str):
    """Run a skill, catching unexpected errors."""
    fn = SKILL_MAP.get(skill_id)
    if not fn:
        await emit(ctx, "failed", f"Unknown skill: {skill_id}")
        return
    try:
        await fn(ctx)
    except asyncio.CancelledError:
        await emit(ctx, "canceled", "Task canceled.")
    except Exception as e:
        await emit(ctx, "failed", f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# JSON-RPC handlers
# ---------------------------------------------------------------------------

def jsonrpc_ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def jsonrpc_err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

def extract_text(message: dict) -> str:
    return " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))


async def handle_send(params: dict, workdir: str) -> dict:
    task_id = params.get("id", str(uuid.uuid4()))
    session_id = params.get("sessionId", str(uuid.uuid4()))
    message = params.get("message", {})
    user_text = extract_text(message)

    if task_id in tasks:
        ctx = tasks[task_id]
        ctx.history.append(message)
        ctx.user_input = user_text
        ctx.input_event.set()
        await asyncio.sleep(0.1)
        return snapshot(ctx)

    # New task
    ctx = TaskContext(task_id=task_id, session_id=session_id, workdir=workdir)
    ctx.api_key = _gemini_key
    ctx.history.append(message)
    tasks[task_id] = ctx

    skill_id = resolve_skill(params)
    await emit(ctx, text=f"Routing to skill: **{skill_id}**")
    ctx.pipeline_task = asyncio.create_task(run_skill(ctx, skill_id))
    await asyncio.sleep(0.2)
    return snapshot(ctx)


async def handle_send_subscribe(params: dict, workdir: str, request: web.Request,
                                response: web.StreamResponse):
    task_id = params.get("id", str(uuid.uuid4()))
    session_id = params.get("sessionId", str(uuid.uuid4()))
    message = params.get("message", {})
    user_text = extract_text(message)
    queue = asyncio.Queue()

    if task_id in tasks:
        ctx = tasks[task_id]
        ctx.history.append(message)
        ctx.user_input = user_text
        ctx.subscribers.append(queue)
        ctx.input_event.set()
    else:
        ctx = TaskContext(task_id=task_id, session_id=session_id, workdir=workdir)
        ctx.api_key = _gemini_key
        ctx.history.append(message)
        ctx.subscribers.append(queue)
        tasks[task_id] = ctx
        skill_id = resolve_skill(params)
        ctx.pipeline_task = asyncio.create_task(run_skill(ctx, skill_id))

    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    try:
        while True:
            data = await asyncio.wait_for(queue.get(), timeout=300)
            line = f"event: update\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            await response.write(line.encode("utf-8"))
            if data.get("status", {}).get("state") in ("completed", "failed", "canceled"):
                break
    except asyncio.TimeoutError:
        pass
    finally:
        if queue in ctx.subscribers:
            ctx.subscribers.remove(queue)
    return response


async def handle_get(params: dict) -> dict:
    task_id = params.get("id", "")
    return snapshot(tasks[task_id]) if task_id in tasks else {"error": "Task not found"}


async def handle_cancel(params: dict) -> dict:
    task_id = params.get("id", "")
    if task_id not in tasks:
        return {"error": "Task not found"}
    ctx = tasks[task_id]
    ctx.cancel_event.set()
    ctx.input_event.set()
    await asyncio.sleep(0.1)
    return snapshot(ctx)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def route_agent_card(request: web.Request) -> web.Response:
    card = {**AGENT_CARD, "url": str(request.url.origin())}
    return web.json_response(card)


async def route_jsonrpc(request: web.Request) -> web.Response:
    workdir = request.app["workdir"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response(jsonrpc_err(None, -32700, "Parse error"), status=400)

    rid = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tasks/send":
        return web.json_response(jsonrpc_ok(rid, await handle_send(params, workdir)))
    elif method == "tasks/sendSubscribe":
        resp = web.StreamResponse()
        await handle_send_subscribe(params, workdir, request, resp)
        return resp
    elif method == "tasks/get":
        return web.json_response(jsonrpc_ok(rid, await handle_get(params)))
    elif method == "tasks/cancel":
        return web.json_response(jsonrpc_ok(rid, await handle_cancel(params)))
    else:
        return web.json_response(jsonrpc_err(rid, -32601, f"Unknown method: {method}"), status=400)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app(workdir: str = ".", gemini_key: str = "") -> web.Application:
    global _gemini_key
    _gemini_key = gemini_key
    app = web.Application()
    app["workdir"] = os.path.abspath(workdir)
    app.router.add_get("/.well-known/agent.json", route_agent_card)
    app.router.add_post("/", route_jsonrpc)
    return app


def main():
    p = argparse.ArgumentParser(description="A2A Meeting Transcription Agent")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5001)
    p.add_argument("--workdir", default=".")
    p.add_argument("--gemini-key", default="")
    args = p.parse_args()

    load_env_file()
    key = args.gemini_key or os.environ.get("GEMINI_API_KEY", "")
    app = create_app(args.workdir, key)

    print(f"A2A Transcribe Agent → http://{args.host}:{args.port}")
    print(f"Agent card: http://localhost:{args.port}/.well-known/agent.json")
    print(f"Skills: transcribe, summarize")
    print(f"Workdir: {os.path.abspath(args.workdir)}")
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
