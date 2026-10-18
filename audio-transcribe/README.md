# audio-transcribe

Diarized transcription of meeting audio through the Gemini API, plus an A2A agent that wraps it.

## `transcribe_gemini.py`

No third-party Python packages — stdlib `urllib`, `json` and `ssl` only. It needs `ffmpeg` on PATH to split long recordings.

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

```bash
python3 a2a_agent.py --port 5001          # key from $GEMINI_API_KEY
python3 a2a_agent.py --port 5001 --workdir /path/to/audio
```

It looks for `scripts/transcribe_gemini.py` first and falls back to its own directory, which is the path that applies here. Note it pins `gemini-2.5-pro` independently of the transcription script's default.

## `skills/transcribe/`

A [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) driving the end-to-end pipeline: discover audio, transcribe, validate attributions, summarize, then validate the summary against the transcript. Copy it into `.claude/skills/` to use it.

## Known limits

- **Speakers must be named up front.** There is no attempt to discover how many voices are present; give it the list.
- **Attribution across a chunk boundary is the weak point.** The overlap helps and does not eliminate it — a speaker change inside the overlap can be attributed twice or to the wrong person. This is what the validation pass exists for.
- **No retry or resume.** A failed chunk fails the run; long files start over.
- **Diarization quality drops with crosstalk** and with speakers of similar pitch, which is where `--speaker-hints` earns its place.
- **Cost scales with audio length**, and a validation pass re-reads the audio.
- **The A2A agent is a demonstration.** No authentication, no persistence, no concurrency limit. Don't put it on a public port.
