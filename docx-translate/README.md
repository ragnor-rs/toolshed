# docx-translate

Translate a Russian `.docx` to Georgian while keeping the document intact — styles, clause numbering, line breaks inside paragraphs, and the signature table.

[`TRANSLATION_GUIDE.md`](TRANSLATION_GUIDE.md) is the working procedure: subagent prompts, the derived glossary with Georgian Labor Code citations, the Russicisms to reject, and the technical gotchas. Read it before changing anything.

## Install

```bash
pip3 install -r requirements.txt
```

`json_repair` is optional. Without it `fix_json.py` falls back to a regex repair that handles the same cases slightly less robustly.

## Run

The whole pipeline, end to end, on the synthetic sample — no API key needed:

```bash
python3 sample/make_sample.py        # writes a synthetic source.docx to try on
python3 extract_sections.py          # source.docx -> sections/section_NN.json + table_cells.json
python3 sample/sample_translation.py # stand-in for the LLM step — sample only, see below
python3 fix_json.py                  # repair JSON broken by Georgian „" quote marks
python3 stitch_docx.py               # -> "variant N.docx", N auto-incremented
python3 verify_structure.py          # compare source against the latest variant; exit 1 on errors
```

That ends with `ALL CHECKS PASSED` and a `variant 1.docx` you can open.

For a real document, drop your own `source.docx` beside the scripts and replace step 3 with the actual translation. Everything derived from your file is gitignored.

The translation step is deliberately not a script. It is a prompt plus a glossary, run as several parallel agents, and [`TRANSLATION_GUIDE.md`](TRANSLATION_GUIDE.md) holds both. `sample/sample_translation.py` exists only so the pipeline is runnable without an API key: it is a fixed lookup table for the synthetic sample, and it exits with an error rather than guessing if it is pointed at any other document.

## What verification covers

| Check | Catches |
| --- | --- |
| Paragraph, table and cell counts | Structural drift, dropped or merged paragraphs |
| Heading styles, position by position | `Heading 1` silently flattened to body text |
| Every clause number (`1.1.`, `8.1.2.`) | A missing or renumbered clause |
| Numbered section headings | A whole section lost |
| Non-empty source → non-empty output | Silently skipped paragraphs |
| Table cell content | A signature block nobody translated |
| Forbidden-term list | Russicisms, and terms that aren't the Labor Code's |
| Required-term list | A term the document must contain and doesn't |
| Russian month names | The single most common residue |

Verification is structural and lexical. It cannot tell you the translation is *correct* — it tells you nothing was dropped, renumbered, or phrased with a term the Code doesn't use. Meaning still needs a human who reads both languages.

### Checking that the checks work

A verifier nobody has watched fail is a verifier nobody should trust. `sample/break_translation.py` damages a clean sample translation in four specific ways — a forbidden Russicism, an untranslated Russian month, a dropped paragraph, and a renumbered clause — each aimed at a different check:

```bash
python3 sample/sample_translation.py   # clean translation
python3 sample/break_translation.py    # inject all four defects
python3 stitch_docx.py
python3 verify_structure.py ; echo "exit=$?"   # must be exit=1, naming all four
python3 sample/sample_translation.py   # undo
```

If that exits 0, something is wrong with the verifier. `--defect forbidden-term` injects one at a time.

One limit the exercise makes obvious: the forbidden-term check is a literal substring match, and Georgian is heavily inflected. `თანამდებობრივი ინსტრუქცია` is caught; the declined `თანამდებობრივ ინსტრუქციას` is not. The list catches the nominative forms an LLM usually produces, not every form it could.

## Known limits

- **Georgian employment contracts only**, as far as the terminology goes. The glossary and the forbidden/required lists in `verify_structure.py` are specific to that document type. Replace them for anything else; the extract/stitch/verify machinery is document-agnostic.
- **`stitch_docx.py` clones the first run's formatting** across the whole paragraph. A paragraph mixing bold and regular text mid-sentence comes out uniformly in the first run's style.
- **Section splitting is by character count**, not by clause boundary, so a section can split mid-clause and the agent translating it loses that context.
- **No retry.** If an agent returns malformed JSON that `fix_json.py` can't repair, you re-run that section by hand.
- **`verify_structure.py` requires equal paragraph counts** for its position-by-position checks. When counts differ it reports the mismatch and the `zip` truncates, so later paragraphs go unchecked — fix the count first, then re-run.
