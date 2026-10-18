---
name: transcribe
description: Transcribe audio files with speaker diarization, validate attributions, create summary, and validate summary against source. Use when user has audio recordings to transcribe and analyze.
argument-hint: <audio-files-glob> [--speakers "Name1,Name2,..."] [--speaker-hints "Name1=description;Name2=description"] [--language ru|en|...] [--gemini-key KEY] [--skip-summary]
---

# Audio Transcription & Analysis Skill

End-to-end pipeline: audio → diarized transcription → attribution validation → summary → summary validation.

The transcription script lives in `audio-transcribe/transcribe_gemini.py`, two levels above this skill. It is also invoked by `a2a_agent.py`, which looks for it at `scripts/transcribe_gemini.py` first and falls back to its own directory — the fallback is the path that applies in this layout.

## Pipeline Steps

### STEP 1: DISCOVER & PREPARE
1. Find audio files matching `$ARGUMENTS` glob pattern (e.g., `*.m4a`, `audio*.mp3`)
2. If no files found, ask the user for the path
3. Check file formats — convert to mp3 if needed (using ffmpeg)
4. Report: number of files, durations, total duration

### STEP 2: GET CONFIGURATION
Ask the user (if not provided via arguments):
- **Speaker names**: Who are the speakers? (e.g., "Анна, Иван, Пётр, Мария")
- **Speaker hints**: Brief description of each speaker's role/voice/topics (helps diarization)
- **Language**: Primary language of the audio (default: auto-detect)
- **API key**: Gemini API key (check env `GEMINI_API_KEY` first, then ask)

### STEP 3: TRANSCRIBE WITH DIARIZATION
Use the helper script in `audio-transcribe/` (`transcribe_gemini.py`):

```bash
python3 transcribe_gemini.py \
  --file <audio_file> \
  --speakers "Name1,Name2,..." \
  --speaker-hints "Name1=description;Name2=description" \
  --language <lang> \
  --api-key <key> \
  --output <output.txt>
```

Run all files in parallel where possible. The script:
- Uploads audio to Gemini Files API
- Sends to gemini-3.1-flash-lite-preview with a carefully crafted prompt
- Extracts and formats the transcript with `[HH:MM:SS] Speaker: text` format
- Saves to `<original_name>.txt` next to the audio file

### STEP 4: ATTRIBUTION VALIDATION
After transcription completes for each file:
1. Extract a proportional number of random substantial utterances (10+ words) per file — roughly 1 sample per 2-3 minutes of audio, minimum 5 — spread across all speakers
2. Present them to the user in a table format:
   ```
   | # | Timestamp | Speaker | Text (first 100 chars) |
   ```
3. Ask user: "Which attributions are wrong? (e.g., '#3 is actually Пётр, #7 is Анна')"
4. Apply corrections with Edit tool (find-replace speaker names on those lines)
5. If user reports issues, offer to re-sample and check again
6. Continue until user confirms attributions are acceptable

### STEP 5: CREATE SUMMARY
Generate a structured summary file (`summary.txt`) that includes:
- **Header**: list of files, participants, topics
- **Per-file section** with:
  - Context and setting
  - Numbered key points, each attributed to speaker(s) with direct quotes where valuable
  - Conclusions per file
- **Cross-file section** with:
  - Overall conclusions
  - Team roles (who does what)
  - Open questions
- **Important**: Every claim must be traceable to a specific speaker and approximate timestamp

### STEP 6: SUMMARY VALIDATION
Systematically verify the summary against source transcriptions:
1. Read the summary file
2. For EACH numbered point:
   - Find the corresponding section in the transcript
   - Verify the speaker attribution is correct
   - Verify the content is accurate (no hallucinated details)
   - Verify direct quotes are actual quotes
3. Report findings:
   - ✅ Verified points (with source timestamp)
   - ❌ Issues found (hallucination, wrong speaker, embellishment)
4. Fix any issues found
5. Check for significant content MISSED from the transcripts
6. Add missing points if found
7. Re-verify the additions

### OUTPUT FILES
For each input audio file `audioXXX.m4a`:
- `audioXXX.txt` — diarized transcription
- `audioXXX.backup.txt` — backup copy of transcription

Plus:
- `summary.txt` — validated summary of all files

## Error Handling
- If Gemini API fails (quota, timeout), suggest alternatives: try gemini-2.5-flash, or fall back to OpenAI gpt-4o-transcribe-diarize
- If audio is too long for Gemini (>9.5 hours), split into chunks
- If speaker identification is poor, suggest user provide audio reference clips for OpenAI's known_speaker_references feature

## Quality Standards
- Transcription must preserve ALL spoken words — no summarization or skipping
- Speaker labels must be real names, not letters (A, B, C)
- Summary must not contain any claims not supported by the transcription
- Every summary point must reference who said it
- Direct quotes in summary must be actual quotes from the transcript
