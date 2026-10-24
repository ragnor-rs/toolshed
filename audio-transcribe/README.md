# audio-transcribe

Diarized transcription of meeting audio through the Gemini API, plus an A2A agent that wraps it.

## `transcribe_gemini.py`

No third-party Python packages — stdlib `urllib`, `json` and `ssl` only. It needs `ffmpeg` on PATH to split long recordings, and a Gemini API key from `--api-key`, `$GEMINI_API_KEY`, or a `.env` file in the repository root. The `.env` reader is ~30 lines of stdlib rather than `python-dotenv`, because having no third-party dependencies is the point of this script.

```bash
python3 transcribe_gemini.py \
  --file meeting.m4a \
  --speakers "Anna,Ivan" \
  --speaker-hints "Anna=runs the call, asks most questions;Ivan=answers, heavy accent" \
  --language ru \
  --output transcript.txt
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--file` | required | Audio file |
| `--speakers` | required | Comma-separated names. Naming them is what makes diarization usable |
| `--speaker-hints` | — | `name=hint;name=hint`. Worth the effort when two voices are close |
| `--language` | `ru` | Language code |
| `--model` | `gemini-3.1-flash-lite-preview` | |
| `--chunk-minutes` | `10` | Split size for long audio |
| `--overlap-seconds` | `15` | Overlap between chunks, so a sentence spanning a cut survives |
| `--api-key` | `$GEMINI_API_KEY` | Prefer the environment variable |

Output is a timestamped transcript: `[00:04:12] Anna: ...`.

## `a2a_agent.py`

The same transcription exposed as an [A2A protocol](https://a2a-protocol.org) agent, written to demonstrate three protocol features rather than to be deployed:

1. **Tool use** — shells out to `transcribe_gemini.py` as an external tool.
2. **Decision making** — decides for itself whether a speaker-attribution validation pass is worth running, from the audio duration and an effort parameter.
3. **Long-running operations** — SSE streaming, `input-required` states, and a multi-step task lifecycle.

Two skills: `transcribe` (audio → diarized transcript, optionally validated) and `summarize` (transcript → structured summary, optionally fact-checked against the source).

Unlike the script, the agent does have a dependency — `aiohttp`:

```bash
pip3 install -r requirements.txt
python3 a2a_agent.py --port 5001          # key from $GEMINI_API_KEY
python3 a2a_agent.py --port 5001 --workdir /path/to/audio
```

The agent card is then at `http://localhost:5001/.well-known/agent.json`, and tasks go to `POST /` as JSON-RPC — `tasks/send`, `tasks/sendSubscribe` (SSE), `tasks/get`, `tasks/cancel`:

```bash
curl -s http://localhost:5001/.well-known/agent.json
curl -s -X POST http://localhost:5001/ -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"tasks/send",
  "params":{"id":"t1","skill":"transcribe",
            "message":{"role":"user","parts":[{"type":"text","text":"transcribe"}]}}}'
```

The first reply comes back `input-required`, asking for speakers; send the answer with the same task `id` to continue.

It looks for `scripts/transcribe_gemini.py` first and falls back to its own directory, which is the path that applies here. Note it pins `gemini-2.5-pro` for its own summary and fact-check calls, independently of the transcription script's default.

## `skills/transcribe/`

A [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) driving the end-to-end pipeline: discover audio, transcribe, validate attributions, summarize, then validate the summary against the transcript. Copy it into `.claude/skills/` to use it.

## Known limits

- **Speakers must be named up front.** There is no attempt to discover how many voices are present; give it the list.
- **Attribution across a chunk boundary is the weak point.** The overlap helps and does not eliminate it — a speaker change inside the overlap can be attributed twice or to the wrong person. This is what the validation pass exists for.
- **Splicing is a text-similarity heuristic, bounded by the overlap window.** Chunks are joined by finding the best speaker+text match within the time range the two chunks actually share. Restricting the search to that range matters: an unbounded search can match a repeated phrase far from the seam, and splice away everything before it. The bound makes that impossible, but the splice point inside the window is still a guess — check the `splice_found` / `splice_fallback` events in the stderr log, and compare the line count against what you expect. `splice_fallback_ts` means content matching failed and timestamps were used instead.
- **No retry or resume.** A failed chunk fails the run; long files start over.
- **Chunking depends entirely on `ffprobe` reporting a duration.** If `ffprobe` is missing or broken, the duration reads as 0, chunking is skipped and the whole file goes up as one request. It warns on stderr when this happens rather than failing, so check for that warning before trusting a long transcript. `ffprobe -version` is the thing to verify.
- **Diarization quality drops with crosstalk** and with speakers of similar pitch, which is where `--speaker-hints` earns its place.
- **Cost scales with audio length**, and a validation pass re-reads the audio.
- **The A2A agent is a demonstration.** No authentication, no persistence, no concurrency limit. Don't put it on a public port.
