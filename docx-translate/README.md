# docx-translate

Translate a Russian `.docx` to Georgian while keeping the document intact — styles, clause numbering, line breaks inside paragraphs, and the signature table.

[`TRANSLATION_GUIDE.md`](TRANSLATION_GUIDE.md) is the working procedure: subagent prompts, the derived glossary with Georgian Labor Code citations, the Russicisms to reject, and the technical gotchas. Read it before changing anything.

## Install

```bash
pip3 install -r requirements.txt
```

`json_repair` is optional. Without it `fix_json.py` falls back to a regex repair that handles the same cases slightly less robustly.

## Run

```bash
python3 sample/make_sample.py   # writes a synthetic source.docx to try on
python3 extract_sections.py     # source.docx -> sections/section_NN.json + table_cells.json
#                                 ... translate each section with an LLM agent,
#                                 ... writing sections/translated_NN.json and translated_table.json
python3 fix_json.py             # repair JSON broken by Georgian „" quote marks
python3 stitch_docx.py          # -> "variant N.docx", N auto-incremented
python3 verify_structure.py     # compare source against the latest variant; exit 1 on errors
```

Drop your own `source.docx` beside the scripts to use it for real. Everything derived from it is gitignored.

The translation step itself is deliberately not a script. It is a prompt plus a glossary, run as several parallel agents, and the guide holds both.

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

## Known limits

- **Georgian employment contracts only**, as far as the terminology goes. The glossary and the forbidden/required lists in `verify_structure.py` are specific to that document type. Replace them for anything else; the extract/stitch/verify machinery is document-agnostic.
- **`stitch_docx.py` clones the first run's formatting** across the whole paragraph. A paragraph mixing bold and regular text mid-sentence comes out uniformly in the first run's style.
- **Section splitting is by character count**, not by clause boundary, so a section can split mid-clause and the agent translating it loses that context.
- **No retry.** If an agent returns malformed JSON that `fix_json.py` can't repair, you re-run that section by hand.
- **`verify_structure.py` requires equal paragraph counts** for its position-by-position checks. When counts differ it reports the mismatch and the `zip` truncates, so later paragraphs go unchecked — fix the count first, then re-run.
