#!/usr/bin/env python3
"""
Transcribe audio files with speaker diarization through the Gemini API.

Zero external dependencies — uses only stdlib (urllib, json, ssl).
Splits long audio into chunks via ffmpeg for reliable processing.

The model is a flag (--model); see `DEFAULT_MODEL` below for the default.

Usage:
    python3 transcribe_gemini.py \
        --file audio.mp3 \
        --speakers "Анна,Иван,Пётр,Мария" \
        --speaker-hints "Анна=main presenter strategy;Иван=technical expert" \
        --language ru \
        --api-key AIza... \
        --output audio.txt \
        --chunk-minutes 15
"""

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = ssl.create_default_context()


DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


def _find_env_file() -> str | None:
    """Nearest .env, searching up from the cwd and then from this script."""
    for start in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        d = os.path.abspath(start)
        while True:
            candidate = os.path.join(d, ".env")
            if os.path.isfile(candidate):
                return candidate
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def load_env_file() -> None:
    """Populate os.environ from the nearest .env file.

    Hand-rolled rather than python-dotenv on purpose: this script has no third-party
    dependencies and that is the point of it. A real environment variable always wins
    over the file, matching dotenv's own precedence.
    """
    path = _find_env_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}


def log(event: str, **kwargs):
    """Write structured progress event to stderr."""
    data = {"event": event, **kwargs}
    print(json.dumps(data, ensure_ascii=False), file=sys.stderr, flush=True)


def get_mime_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return MIME_TYPES.get(ext, "audio/mpeg")


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds using ffprobe. Returns 0 if it cannot be read."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception as e:
        # Worth saying out loud: callers treat 0 as "short enough not to split", so a
        # broken ffprobe silently turns chunking off. A dyld failure from a partial
        # Homebrew upgrade looks exactly like a 0-second file otherwise.
        log("duration_unknown", file=os.path.basename(path), reason=str(e)[:200])
        return 0


def split_audio(path: str, chunk_seconds: int, overlap_seconds: int,
                tmpdir: str) -> list[tuple[str, float]]:
    """Split audio into overlapping chunks using ffmpeg.

    Returns [(chunk_path, start_seconds), ...].
    Each chunk overlaps with the next by overlap_seconds so boundary
    words are captured in both chunks for clean stitching.
    """
    duration = get_audio_duration(path)
    if duration <= 0:
        return [(path, 0)]

    if duration <= chunk_seconds:
        return [(path, 0)]

    ext = os.path.splitext(path)[1]
    chunks = []
    start = 0
    idx = 0
    step = chunk_seconds - overlap_seconds  # advance by less than chunk size

    while start < duration:
        length = min(chunk_seconds, duration - start)
        chunk_path = os.path.join(tmpdir, f"chunk_{idx:03d}{ext}")
        cmd = [
            "ffmpeg", "-y", "-i", path,
            "-ss", str(start),
            "-t", str(length),
            "-c", "copy",
            "-loglevel", "error",
            chunk_path,
        ]
        subprocess.run(cmd, check=True, timeout=120)
        chunks.append((chunk_path, start))
        start += step
        idx += 1
        if start + overlap_seconds >= duration:
            break  # last chunk already covers the rest

    log("split_complete", chunks=len(chunks), duration_seconds=round(duration),
        overlap_seconds=overlap_seconds)
    return chunks


def offset_timestamps(text: str, offset_seconds: float) -> str:
    """Add time offset to all [HH:MM:SS] timestamps in the transcript."""
    if offset_seconds == 0:
        return text

    def add_offset(match):
        h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
        total = h * 3600 + m * 60 + s + int(offset_seconds)
        nh = total // 3600
        nm = (total % 3600) // 60
        ns = total % 60
        return f"[{nh:02d}:{nm:02d}:{ns:02d}]"

    return re.sub(r"\[(\d{2}):(\d{2}):(\d{2})\]", add_offset, text)


def parse_ts_seconds(ts: str) -> int:
    """Parse 'HH:MM:SS' to total seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def extract_speaker_text(line: str) -> tuple[str, str]:
    """Extract (speaker, text) from a transcript line like '[00:01:23] Анна: hello'."""
    m = re.match(r"\[\d{2}:\d{2}:\d{2}\]\s+(.+?):\s+(.+)", line.strip())
    if m:
        return m.group(1), m.group(2)
    return "", ""


def parse_lines(text: str) -> list[str]:
    """Extract non-empty timestamped lines from transcript text."""
    return [l.strip() for l in text.splitlines()
            if re.match(r"\[\d{2}:\d{2}:\d{2}\]", l.strip())]


def text_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity between two strings (0..1)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


def line_ts_seconds(line: str) -> int:
    """Extract timestamp in seconds from a transcript line."""
    m = re.match(r"\[(\d{2}):(\d{2}):(\d{2})\]", line.strip())
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return -1


def find_splice_point(chunk_a_lines: list[str], chunk_b_lines: list[str],
                      overlap_start: float | None = None,
                      overlap_end: float | None = None) -> tuple[int, int]:
    """Find the best content-based splice point between two overlapping chunks.

    Compares lines from the end of chunk_a with lines from the start of chunk_b.
    Looks for matching speaker + similar text. Returns (cut_a, start_b) indices:
    keep chunk_a[:cut_a] and chunk_b[start_b:].

    overlap_start/overlap_end bound the search to the time range the two chunks can
    actually share, in the same timebase as the lines. That bound is not an
    optimisation — without it, a coincidental match early in chunk_a returns cut_a=0
    and silently discards every line accumulated so far. Repeated phrasing in a
    meeting is enough to trigger it.
    """
    # The splice may never cut before the overlap begins: anything earlier than that
    # exists only in chunk_a, so cutting it would drop content outright.
    min_cut_a = 0
    if overlap_start is not None:
        min_cut_a = len(chunk_a_lines)
        for idx, line in enumerate(chunk_a_lines):
            ts = line_ts_seconds(line)
            if ts >= 0 and ts >= overlap_start:
                min_cut_a = idx
                break

    # Search window: last 30 lines of A, but never reaching before the overlap.
    window_start = max(min_cut_a, len(chunk_a_lines) - 30, 0)
    tail_a = chunk_a_lines[window_start:]
    offset_a = window_start
    head_b = chunk_b_lines[:30] if len(chunk_b_lines) > 30 else chunk_b_lines

    best_score = 0
    best_a_idx = len(chunk_a_lines)
    best_b_idx = 0

    for i, line_a in enumerate(tail_a):
        speaker_a, text_a = extract_speaker_text(line_a)
        if not speaker_a:
            continue
        for j, line_b in enumerate(head_b):
            if overlap_end is not None:
                ts_b = line_ts_seconds(line_b)
                if ts_b >= 0 and ts_b > overlap_end:
                    break  # lines are in order; past the overlap there is no match
            speaker_b, text_b = extract_speaker_text(line_b)
            if not speaker_b:
                continue
            if speaker_a != speaker_b:
                continue
            sim = text_similarity(text_a, text_b)
            # >= rather than >, so among equally good matches the latest one in A
            # wins. That is the one which discards the least content.
            if sim > 0 and sim >= best_score:
                best_score = sim
                best_a_idx = offset_a + i
                best_b_idx = j

    if best_score >= 0.4:
        cut_a = max(best_a_idx, min_cut_a)
        log("splice_found", score=round(best_score, 2),
            cut_a=cut_a, start_b=best_b_idx, kept_a=cut_a, total_a=len(chunk_a_lines))
        return cut_a, best_b_idx

    # Fallback: use timestamps. Keep all of A, skip B lines whose
    # timestamps are <= the last timestamp in A.
    last_ts = -1
    for line in reversed(chunk_a_lines):
        last_ts = line_ts_seconds(line)
        if last_ts >= 0:
            break

    if last_ts >= 0:
        skip = 0
        for line in chunk_b_lines:
            ts = line_ts_seconds(line)
            if ts >= 0 and ts <= last_ts:
                skip += 1
            else:
                break
        log("splice_fallback_ts", best_score=round(best_score, 2),
            last_a_ts=last_ts, skipped_b=skip)
        return len(chunk_a_lines), skip

    log("splice_fallback", best_score=round(best_score, 2))
    return len(chunk_a_lines), 0


def dedup_consecutive(lines: list[str]) -> list[str]:
    """Remove consecutive lines where one is a subset of the other.

    Catches cases where the splice point line appears in both chunks
    with slightly different wording, or chunk B starts with a partial
    repeat of chunk A's last line.
    """
    if len(lines) < 2:
        return lines

    result = [lines[0]]
    for i in range(1, len(lines)):
        _, text_prev = extract_speaker_text(result[-1])
        _, text_curr = extract_speaker_text(lines[i])

        if not text_prev or not text_curr:
            result.append(lines[i])
            continue

        # Check if current line starts with the end of previous line
        # (partial overlap from chunk boundary)
        words_prev = text_prev.lower().split()
        words_curr = text_curr.lower().split()

        # If current line's first 5+ words match the last 5+ words of prev line,
        # it's a boundary duplicate
        if len(words_prev) >= 5 and len(words_curr) >= 5:
            overlap_len = min(len(words_prev), len(words_curr), 10)
            tail_prev = words_prev[-overlap_len:]
            head_curr = words_curr[:overlap_len]
            # Check for prefix match of various lengths
            skip = False
            for k in range(min(overlap_len, len(head_curr)), 3, -1):
                if tail_prev[-k:] == head_curr[:k]:
                    skip = True
                    break
            if skip:
                # Merge: keep prev line as-is, skip this line if it adds nothing new
                # or keep only the new part
                new_words = words_curr[k:]
                if not new_words:
                    continue  # pure duplicate
                # Append new content to previous line
                result[-1] = result[-1].rstrip() + " " + " ".join(
                    text_curr.split()[k:])
                continue

        # Also catch near-identical lines (sim > 0.8)
        sim = text_similarity(text_prev, text_curr)
        if sim >= 0.8:
            # Keep the longer one
            if len(text_curr) > len(text_prev):
                result[-1] = lines[i]
            continue

        result.append(lines[i])

    return result


def stitch_chunks(chunks_text: list[str], chunk_starts: list[float],
                  chunk_seconds: int, overlap_seconds: int) -> str:
    """Stitch overlapping chunks using content-based splice detection.

    For each pair of adjacent chunks, finds matching lines in the overlap
    zone and splices at that point. Falls back to timestamp-based dedup.
    Finally runs dedup pass to catch partial line overlaps.
    """
    if len(chunks_text) <= 1:
        return chunks_text[0] if chunks_text else ""

    # Parse all chunks into line lists
    all_parsed = [parse_lines(t) for t in chunks_text]

    # Start with first chunk's lines
    result = list(all_parsed[0])

    for i in range(1, len(all_parsed)):
        # The region chunk i genuinely shares with everything before it: it begins at
        # chunk i's own start time and lasts for the overlap. Bounding the search to
        # it is what stops a splice from discarding the accumulated transcript.
        overlap_start = overlap_end = None
        if chunk_starts and i < len(chunk_starts) and overlap_seconds > 0:
            overlap_start = chunk_starts[i]
            overlap_end = chunk_starts[i] + overlap_seconds

        cut_a, start_b = find_splice_point(
            result, all_parsed[i], overlap_start, overlap_end)
        chunk_b_kept = all_parsed[i][start_b:]

        # Check boundary: if last line of A and first line of B overlap,
        # the splice matched lines that share audio. Trim B's first line
        # to only keep content that's new beyond what A already has.
        if result[:cut_a] and chunk_b_kept:
            _, text_a_last = extract_speaker_text(result[cut_a - 1])
            _, text_b_first = extract_speaker_text(chunk_b_kept[0])
            if text_a_last and text_b_first:
                sim = text_similarity(text_a_last, text_b_first)
                if sim >= 0.4:
                    # B's first line overlaps with A's last line.
                    # Find where B's text diverges from A's text.
                    words_a = text_a_last.split()
                    words_b = text_b_first.split()
                    # Find longest suffix of A that matches a prefix of B
                    best_k = 0
                    for k in range(1, min(len(words_a), len(words_b)) + 1):
                        if words_a[-k:] == words_b[:k]:
                            best_k = k
                    if best_k > 0:
                        new_content = " ".join(words_b[best_k:])
                        if new_content:
                            # Append new content to A's last line
                            result[cut_a - 1] = result[cut_a - 1].rstrip() + " " + new_content
                            chunk_b_kept = chunk_b_kept[1:]  # drop B's first line
                        else:
                            chunk_b_kept = chunk_b_kept[1:]  # pure duplicate
                    elif sim >= 0.8:
                        # High overlap but no clean prefix match — keep the longer one
                        if len(text_b_first) > len(text_a_last):
                            result[cut_a - 1] = chunk_b_kept[0]
                        chunk_b_kept = chunk_b_kept[1:]

        result = result[:cut_a] + chunk_b_kept

    # Post-stitch dedup pass for any remaining artifacts
    result = dedup_consecutive(result)

    return "\n\n".join(result)


def upload_file(api_key: str, file_path: str) -> str:
    """Upload audio file to Gemini Files API, return file URI."""
    mime_type = get_mime_type(file_path)
    log("upload_start", file=os.path.basename(file_path), mime_type=mime_type)

    with open(file_path, "rb") as f:
        data = f.read()

    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-Command": "upload, finalize",
            "Content-Type": mime_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300, context=_ssl_context) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # The body says why — most often an invalid or unauthorized API key.
        try:
            body = e.read().decode(errors="replace").strip()
        except Exception:
            body = ""
        raise RuntimeError(f"Upload failed ({e}): {body[:600]}") from e

    file_uri = result["file"]["uri"]
    state = result["file"]["state"]

    if state != "ACTIVE":
        log("upload_waiting", state=state)
        file_name = result["file"]["name"]
        for _ in range(30):
            time.sleep(2)
            check_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
            check_req = urllib.request.Request(check_url)
            with urllib.request.urlopen(check_req, timeout=30, context=_ssl_context) as r:
                info = json.loads(r.read())
            if info.get("state") == "ACTIVE":
                break
            if info.get("state") == "FAILED":
                raise RuntimeError(f"File processing failed: {file_name}")

    log("upload_complete", file_uri=file_uri)
    return file_uri


def build_prompt(speakers: list[str], speaker_hints: dict, language: str,
                 chunk_index: int = 0, total_chunks: int = 1) -> str:
    """Build the transcription prompt."""
    speaker_list = ", ".join(speakers)

    hints_text = ""
    if speaker_hints:
        hints_text = "\n\nSpeaker descriptions to help with identification:\n"
        for name, hint in speaker_hints.items():
            hints_text += f"- {name}: {hint}\n"

    chunk_note = ""
    if total_chunks > 1:
        chunk_note = f"\n\nThis is part {chunk_index + 1} of {total_chunks} of the recording. Start timestamps from 00:00 for this chunk."

    lang_upper = language.upper() if language else ""
    return f"""Transcribe this {lang_upper} audio recording with {len(speakers)} speakers: {speaker_list}.
{hints_text}{chunk_note}
Output format: one line per utterance, with timestamp and speaker name:
MM:SS Speaker: text

Rules:
- Add a blank line between speaker changes
- Use proper {lang_upper} punctuation and capitalization
- Transcribe EVERY word spoken — do not summarize, skip, or paraphrase anything
- Include filler words (э-э, а-а, ну) as they appear in speech
- Use the exact speaker names provided: {speaker_list}
- If unsure which speaker, use your best judgment based on voice characteristics and context
- Timestamps in MM:SS format (minutes:seconds)
- Do not add any commentary, headers, or markdown formatting
- Start from 00:00 and cover the entire recording"""


def transcribe(
    api_key: str,
    file_uri: str,
    prompt: str,
    mime_type: str,
    model: str = DEFAULT_MODEL,
    max_retries: int = 5,
) -> str:
    """Send transcription request to Gemini API. Retries on transient errors."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 65000,
        },
    }

    data = json.dumps(payload).encode()

    for attempt in range(1, max_retries + 1):
        try:
            log("transcribe_start", model=model, attempt=attempt)

            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1800, context=_ssl_context) as resp:
                chunks = []
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))

            if "error" in result:
                raise RuntimeError(f"Gemini API error: {result['error']['message']}")

            candidates = result.get("candidates", [])
            if not candidates:
                raise RuntimeError(f"No candidates returned: {json.dumps(result)[:500]}")

            candidate = candidates[0]
            if "content" not in candidate or "parts" not in candidate.get("content", {}):
                finish_reason = candidate.get("finishReason", "unknown")
                raise RuntimeError(
                    f"No content in response (finishReason={finish_reason}): "
                    f"{json.dumps(candidate)[:500]}"
                )

            text = candidate["content"]["parts"][0]["text"]
            if not text or not text.strip():
                raise RuntimeError("Empty response from Gemini")

            return text

        except (urllib.error.HTTPError, RuntimeError, urllib.error.URLError, TimeoutError, OSError) as e:
            error_str = str(e)
            # An HTTPError carries Google's actual explanation in the body — "API key
            # not valid", a quota name, the rejected model id. str(e) is just the code.
            if isinstance(e, urllib.error.HTTPError):
                try:
                    body = e.read().decode(errors="replace").strip()
                except Exception:
                    body = ""
                if body:
                    error_str = f"{error_str}: {body[:600]}"
            is_transient = any(
                code in error_str
                for code in ["503", "429", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "Empty response", "timed out"]
            )
            if is_transient and attempt < max_retries:
                wait = 2 ** attempt
                log("error", message=error_str, retry=attempt, wait_seconds=wait)
                time.sleep(wait)
                continue
            raise RuntimeError(error_str) from e


def fix_timestamps(text: str) -> str:
    """Convert Gemini's native MM:SS timestamps to [HH:MM:SS] format.

    Handles various formats Gemini may produce:
      MM:SS      → [00:MM:SS]       (native format)
      [MM:SS]    → [00:MM:SS]
      HH:MM:SS   → [HH:MM:SS]
      [HH:MM:SS] → [HH:MM:SS]
    """
    def convert_ts(match):
        raw = match.group(1)
        parts = raw.split(":")
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            h = m // 60
            m = m % 60
            return f"[{h:02d}:{m:02d}:{s:02d}]"
        elif len(parts) == 3:
            a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
            return f"[{a:02d}:{b:02d}:{c:02d}]"
        return match.group(0)

    # Match MM:SS or HH:MM:SS with optional brackets, at line start
    text = re.sub(r"^\[?(\d{1,3}:\d{2}(?::\d{2})?)\]?(?=\s)", convert_ts, text, flags=re.MULTILINE)
    return text


def clamp_timestamps(text: str, max_seconds: float) -> str:
    """Discard lines with timestamps beyond the chunk's actual duration.

    Gemini sometimes hallucinates timestamps that exceed the audio length.
    Handles both MM:SS and HH:MM:SS formats (pre- and post-conversion).
    """
    if max_seconds <= 0:
        return text
    lines = text.splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        ts_sec = None
        # Try HH:MM:SS
        m = re.match(r"\[?(\d{1,2}):(\d{1,2}):(\d{1,2})\]?", stripped)
        if m:
            ts_sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        else:
            # Try MM:SS
            m = re.match(r"\[?(\d{1,3}):(\d{1,2})\]?", stripped)
            if m:
                ts_sec = int(m.group(1)) * 60 + int(m.group(2))
        if ts_sec is not None and ts_sec > max_seconds + 10:
            continue
        kept.append(line)
    return "\n".join(kept)


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE)
    return text.strip()


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with Gemini")
    parser.add_argument("--file", required=True, help="Audio file path")
    parser.add_argument("--speakers", required=True, help="Comma-separated speaker names")
    parser.add_argument("--speaker-hints", default="", help="name1=hint1;name2=hint2 format (semicolon-separated)")
    parser.add_argument("--language", default="ru", help="Language code (e.g., ru, en)")
    parser.add_argument("--api-key", default="", help="Gemini API key")
    parser.add_argument("--output", default="", help="Output file path")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--chunk-minutes", type=int, default=10,
                        help="Split audio into chunks of N minutes (0 = no split, default: 10)")
    parser.add_argument("--overlap-seconds", type=int, default=15,
                        help="Overlap between chunks in seconds (default: 15)")
    args = parser.parse_args()

    load_env_file()
    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("Error: No API key. Use --api-key, set GEMINI_API_KEY, or put "
              "GEMINI_API_KEY=... in a .env file beside the repo root.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    speakers = [s.strip() for s in args.speakers.split(",") if s.strip()]
    speaker_hints = {}
    if args.speaker_hints:
        for pair in args.speaker_hints.split(";"):
            if "=" in pair:
                name, hint = pair.split("=", 1)
                speaker_hints[name.strip()] = hint.strip()

    output_path = args.output or os.path.splitext(args.file)[0] + ".txt"
    mime_type = get_mime_type(args.file)
    chunk_seconds = args.chunk_minutes * 60
    overlap_seconds = args.overlap_seconds

    # split_audio advances by (chunk - overlap); a non-positive step never terminates.
    if chunk_seconds > 0 and overlap_seconds >= chunk_seconds:
        print(f"Error: --overlap-seconds ({overlap_seconds}) must be less than the "
              f"chunk length ({chunk_seconds}s from --chunk-minutes "
              f"{args.chunk_minutes}).", file=sys.stderr)
        sys.exit(1)
    if overlap_seconds < 0:
        print("Error: --overlap-seconds cannot be negative.", file=sys.stderr)
        sys.exit(1)

    # Split into chunks if needed
    tmpdir = None
    if chunk_seconds > 0 and shutil.which("ffmpeg"):
        duration = get_audio_duration(args.file)
        if duration > chunk_seconds:
            tmpdir = tempfile.mkdtemp(prefix="transcribe_")
            log("split_start", duration_seconds=round(duration),
                chunk_minutes=args.chunk_minutes, overlap_seconds=overlap_seconds)
            audio_chunks = split_audio(args.file, chunk_seconds, overlap_seconds, tmpdir)
        else:
            if duration <= 0:
                print(
                    "Warning: ffprobe could not read the duration, so the file is being "
                    "sent as a single request with no chunking. If this is a long "
                    "recording, expect a truncated or failed transcription. Check that "
                    "ffmpeg/ffprobe actually run (`ffprobe -version`), or pass "
                    "--chunk-minutes 0 to make this explicit.",
                    file=sys.stderr,
                )
            audio_chunks = [(args.file, 0)]
    elif chunk_seconds > 0:
        print("Warning: ffmpeg not on PATH — sending the file as a single request "
              "with no chunking.", file=sys.stderr)
        audio_chunks = [(args.file, 0)]
    else:
        audio_chunks = [(args.file, 0)]

    # Transcribe each chunk
    all_text_parts = []
    chunk_starts = [start for _, start in audio_chunks]
    total_chunks = len(audio_chunks)

    try:
        for i, (chunk_path, offset_sec) in enumerate(audio_chunks):
            if total_chunks > 1:
                log("chunk_start", chunk=i + 1, total=total_chunks, offset_seconds=round(offset_sec))

            file_uri = upload_file(api_key, chunk_path)
            chunk_mime = get_mime_type(chunk_path)
            prompt = build_prompt(speakers, speaker_hints, args.language, i, total_chunks)
            raw_text = transcribe(api_key, file_uri, prompt, chunk_mime, model=args.model)

            text_chunk = strip_markdown_fences(raw_text)
            text_chunk = fix_timestamps(text_chunk)
            # Discard hallucinated timestamps beyond chunk duration
            chunk_duration = get_audio_duration(chunk_path) if chunk_path != args.file else 0
            if chunk_duration > 0:
                text_chunk = clamp_timestamps(text_chunk, chunk_duration)
            text_chunk = offset_timestamps(text_chunk, offset_sec)

            all_text_parts.append(text_chunk)
            if total_chunks > 1:
                log("chunk_complete", chunk=i + 1, total=total_chunks)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Stitch chunks with overlap dedup or simple join
    if total_chunks > 1 and overlap_seconds > 0:
        text = stitch_chunks(all_text_parts, chunk_starts, chunk_seconds, overlap_seconds)
        log("stitch_complete", chunks=total_chunks)
    else:
        text = "\n\n".join(all_text_parts)

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    # Save backup
    backup_path = os.path.splitext(output_path)[0] + ".backup.txt"
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(text)

    # Stats
    speaker_counts = Counter()
    for line in text.split("\n"):
        m = re.match(r"\[\d+:\d+:\d+\]\s+(.+?):", line)
        if m:
            speaker_counts[m.group(1)] += 1

    log("transcribe_complete", output=output_path, lines=text.count("\n") + 1,
        speakers=dict(speaker_counts))
    print(output_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        # A traceback here is never the useful part — the API's message is.
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
