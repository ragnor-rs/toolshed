#!/usr/bin/env python3
"""Tests for the chunk-stitching logic in transcribe_gemini.py.

No API key, no ffmpeg, no network — these are the pure string functions that run on
every multi-chunk transcription.

    python3 test_stitching.py

The regression at the bottom is the one that matters. Splicing used to search for the
best text match anywhere in the accumulated transcript, unbounded by the overlap. A
repeated phrase could therefore match far from the seam, return cut_a=0, and discard
every line gathered so far — no error, no warning, just a plausible-looking transcript
missing most of the recording. It cost 75% of a real 7.6-minute run.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "transcribe_gemini", os.path.join(HERE, "transcribe_gemini.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

failures = []


def check(name, got, want):
    if got == want:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}\n      got:  {got!r}\n      want: {want!r}")
        failures.append(name)


# --------------------------------------------------------------- timestamp handling
check("fix_timestamps MM:SS -> [HH:MM:SS]",
      m.fix_timestamps("05:30 Anna: hello"), "[00:05:30] Anna: hello")
check("fix_timestamps rolls minutes past 59 into hours",
      m.fix_timestamps("75:30 Anna: hi"), "[01:15:30] Anna: hi")
check("fix_timestamps leaves [HH:MM:SS] alone",
      m.fix_timestamps("[01:02:03] Anna: hi"), "[01:02:03] Anna: hi")
check("offset_timestamps shifts by the chunk start",
      m.offset_timestamps("[00:00:10] Anna: hi", 585), "[00:09:55] Anna: hi")
check("offset_timestamps crosses an hour boundary",
      m.offset_timestamps("[00:10:00] Anna: hi", 3000), "[01:00:00] Anna: hi")
check("offset_timestamps of zero is a no-op",
      m.offset_timestamps("[00:00:10] A: hi", 0), "[00:00:10] A: hi")
check("clamp_timestamps drops lines beyond the chunk (10s grace)",
      m.clamp_timestamps("[00:00:10] A: keep\n[00:09:50] A: keep\n[00:20:00] A: bogus", 600),
      "[00:00:10] A: keep\n[00:09:50] A: keep")

# --------------------------------------------------------------- parsing
check("parse_lines keeps only timestamped lines",
      m.parse_lines("preamble\n[00:00:01] A: x\n\nnoise\n[00:00:05] B: y"),
      ["[00:00:01] A: x", "[00:00:05] B: y"])
check("extract_speaker_text splits speaker from text",
      m.extract_speaker_text("[00:01:23] Anna: hello there"), ("Anna", "hello there"))

# --------------------------------------------------------------- dedup
check("dedup_consecutive collapses an exact repeat",
      len(m.dedup_consecutive([
          "[00:00:01] Anna: the quick brown fox jumps over the lazy dog",
          "[00:00:01] Anna: the quick brown fox jumps over the lazy dog"])), 1)
check("dedup_consecutive keeps genuinely different lines",
      len(m.dedup_consecutive([
          "[00:00:01] Anna: we should ship the release on Thursday afternoon",
          "[00:00:09] Ivan: I disagree, the staging soak test is not finished"])), 2)

# --------------------------------------------------------------- splice bounding
# A 28-line transcript where line 0 is identical to a line in chunk B. Unbounded, the
# best match is at index 0 and everything is thrown away.
_a = [f"[00:0{i // 10}:{i % 10 * 6:02d}] Anna: utterance number {i} with enough words here"
      for i in range(28)]
_b = ["[00:01:45] Anna: utterance number 0 with enough words here",
      "[00:01:50] Ivan: something new entirely in chunk b now"]
_cut, _ = m.find_splice_point(_a, _b, overlap_start=105, overlap_end=120)
check("splice never cuts before the overlap begins", _cut >= 27, True)

# --------------------------------------------------------------- full regression
DIALOGUE = [
    ("Anna", "Good morning Ivan, let us start the weekly platform review please."),
    ("Ivan", "Morning, I have three items and the first is the billing migration."),
    ("Anna", "Go ahead, did the migration finish over the weekend as planned?"),
    ("Ivan", "It finished on Saturday morning and we moved eighteen thousand records."),
    ("Anna", "Were there any validation failures during the cutover itself?"),
    ("Ivan", "Forty two rows failed, every one of them missing a currency code."),
    ("Anna", "Small enough to repair by hand, so who is picking that up?"),
    ("Ivan", "Priya is doing it on Tuesday, she estimates two hours of work."),
]

DURATION = 400.0
CHUNK, OVERLAP = 120, 15

# Repeat the dialogue so the same phrasing recurs — the condition that triggered the bug.
script = DIALOGUE * 6
step = DURATION / len(script)
timed = [(i * step, spk, text) for i, (spk, text) in enumerate(script)]

starts, s = [], 0
while s < DURATION:
    starts.append(s)
    if s + (CHUNK - OVERLAP) + OVERLAP >= DURATION:
        break
    s += CHUNK - OVERLAP


def _ts(sec):
    sec = int(sec)
    return f"[{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}]"


# Build each chunk the way Gemini returns it: numbered from 00:00, then offset.
chunk_texts = [
    m.offset_timestamps(
        "\n\n".join(f"{_ts(t - start)} {spk}: {text}"
                    for t, spk, text in timed if start <= t < min(start + CHUNK, DURATION)),
        start)
    for start in starts
]

stitched = m.stitch_chunks(chunk_texts, starts, CHUNK, OVERLAP)
lines = [l for l in stitched.splitlines() if l.strip()]
stamps = [m.line_ts_seconds(l) for l in lines]

check(f"stitching {len(starts)} chunks keeps at least 90% of {len(timed)} utterances",
      len(lines) >= len(timed) * 0.9, True)
check("stitched transcript starts at the beginning of the recording",
      stamps[0] <= 10, True)
check("stitched timestamps increase monotonically", stamps == sorted(stamps), True)
check("stitched transcript reaches the end of the recording",
      stamps[-1] >= DURATION - 30, True)

print()
if failures:
    print(f"{len(failures)} failed: {failures}")
    sys.exit(1)
print(f"all checks passed ({len(lines)}/{len(timed)} utterances kept across "
      f"{len(starts)} chunks)")
