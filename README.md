# toolshed

Two small tools that came out of real jobs and were worth keeping. They share nothing but an author and a habit: let an LLM do the language work, and write a deterministic script to check that it did.

Neither is a product. Both run, both have known limits, and both are documented with the limits included.

## [`docx-translate/`](docx-translate/) — structure-preserving DOCX translation

Translates a Russian `.docx` to Georgian without losing the document. Extracts paragraphs with their style metadata, splits them into sections, fans the sections out to parallel LLM agents, stitches the results back into the original file, then verifies the output against the source — paragraph count, table count, heading styles, every clause number, dropped translations, and a forbidden-term list.

The interesting part is not the translation, it's the verification. An LLM translating a contract will quietly rewrite an address into something more plausible, leave one month name in the source language, skip a table nobody told it about, or reach for a term that is a calque from Russian rather than the one the Georgian Labor Code actually uses. All four happened. `verify_structure.py` catches each of them mechanically, and [`TRANSLATION_GUIDE.md`](docx-translate/TRANSLATION_GUIDE.md) records how the glossary was derived — with Labor Code article citations.

The terminology rules are specific to Georgian employment contracts. The pipeline is not.

## [`audio-transcribe/`](audio-transcribe/) — diarized transcription and an A2A agent

`transcribe_gemini.py` transcribes audio with speaker diarization through the Gemini API, splitting long files into chunks with ffmpeg. Zero third-party Python dependencies — stdlib `urllib`, `json` and `ssl` only.

`a2a_agent.py` wraps it as an [A2A protocol](https://a2a-protocol.org) agent, as a working demonstration of three things the protocol exists for: calling an external tool, deciding for itself whether a validation pass is warranted, and running a long operation through SSE streaming with `input-required` states and a multi-step task lifecycle.

## Running them

Both need Python 3.11+. `docx-translate` needs `python-docx` and `lxml`; `audio-transcribe` needs `ffmpeg` on PATH and a Gemini API key, passed by flag or in `GEMINI_API_KEY`.

No key is ever read from a file in this repository, and nothing here should be given one. Pass it through the environment.

## License

[MIT](LICENSE) © Renat Sarymsakov
