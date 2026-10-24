# toolshed

Small tools that came out of real jobs and were worth keeping. They have no theme and no shared dependencies — the only thing they have in common is that each one was built because nothing off the shelf did the job.

None is a product. All of them run, all of them have known limits, and the limits are written down rather than left to be discovered.

## [`docx-translate/`](docx-translate/) — structure-preserving DOCX translation

Translates a Russian `.docx` to Georgian without losing the document. Extracts paragraphs with their style metadata, splits them into sections, fans the sections out to parallel LLM agents, stitches the results back into the original file, then verifies the output against the source — paragraph count, table count, heading styles, every clause number, dropped translations, and a forbidden-term list.

The interesting part is not the translation, it's the verification. An LLM translating a contract will quietly rewrite an address into something more plausible, leave one month name in the source language, skip a table nobody told it about, or reach for a term that is a calque from Russian rather than the one the Georgian Labor Code actually uses. All four happened. `verify_structure.py` catches each of them mechanically, and [`TRANSLATION_GUIDE.md`](docx-translate/TRANSLATION_GUIDE.md) records how the glossary was derived — with Labor Code article citations.

The terminology rules are specific to Georgian employment contracts. The pipeline is not.

## [`audio-transcribe/`](audio-transcribe/) — diarized transcription and an A2A agent

`transcribe_gemini.py` transcribes audio with speaker diarization through the Gemini API, splitting long files into chunks with ffmpeg. Zero third-party Python dependencies — stdlib `urllib`, `json` and `ssl` only.

`a2a_agent.py` wraps it as an [A2A protocol](https://a2a-protocol.org) agent, as a working demonstration of three things the protocol exists for: calling an external tool, deciding for itself whether a validation pass is warranted, and running a long operation through SSE streaming with `input-required` states and a multi-step task lifecycle.

## [`todoist-backup/`](todoist-backup/) — full-account Todoist backup

Reads an entire Todoist account through the API — projects, sections, tasks, comments, labels, filters, reminders, archived projects, completed activity — and writes a ZIP containing all of it plus a restore script generated at backup time, so a backup doesn't depend on this repository still existing.

Todoist's own export is per-project CSV and drops everything that isn't a task row. The detail worth stealing if you write your own: projects, sections and tasks are topologically sorted before being recreated, because creating them in API order fails the moment a subtask comes before its parent.

## Running them

All three need Python 3.11+, and each has its own `requirements.txt`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r docx-translate/requirements.txt    # python-docx, lxml
pip install -r audio-transcribe/requirements.txt  # aiohttp, for the A2A agent only
pip install -r todoist-backup/requirements.txt    # httpx, rich, python-dotenv
```

`docx-translate` runs end to end with no API key — `cd docx-translate && python3 sample/make_sample.py` and follow its README. `audio-transcribe` needs `ffmpeg` on PATH and a Gemini API key; `todoist-backup` needs a Todoist API token.

Keys come from the environment, or from a `.env` file in the repository root:

```
GEMINI_API_KEY=...
TODOIST_API_TOKEN=...
```

`.env` is gitignored at every level, and a real environment variable always wins over it. `chmod 600 .env` — the default `644` leaves your keys readable by every account on the machine. Prefer the file over `export KEY=...` in an interactive shell, which writes the key into your shell history in plaintext.

No key is ever committed, and nothing here should be given a key you cannot rotate.

## License

[MIT](LICENSE) © Renat Sarymsakov
