# Translating a Russian DOCX to Georgian

This is the working procedure the scripts in this directory implement. It was written
against a real job: a fixed-term labor contract (შრომითი ხელშეკრულება) for a
barista-cashier position in Tbilisi — 98 paragraphs, one signature table, ~9300
characters. That document is not in this repository, and the glossary and verification
rules below are the part worth keeping.

The terminology guidance is specific to Georgian employment contracts. For any other
document type, replace the glossary and the forbidden-term list in
`verify_structure.py`; the pipeline itself is document-agnostic.

## Source & Output

- **Input:** `source.docx`, beside the scripts. Run `sample/make_sample.py` to generate a
  synthetic one to try the pipeline on. For that synthetic document only,
  `sample/sample_translation.py` stands in for the agent step below, so the pipeline can
  be run end to end without an API key. It is a fixed lookup table, not a translator.
- **Output:** `variant X.docx`, X = next available number
- **Language:** Russian → Georgian

---

## Pipeline

All scripts use relative paths (project dir auto-detected). Just run them from the project directory.

```
source.docx
    |
    v
[python3 extract_sections.py]
    |  Parses source.docx with python-docx
    |  Extracts 98 paragraphs + 2 table cells with style metadata
    |  Splits paragraphs into ~3000-char sections
    |  Outputs: sections/section_00..03.json + sections/table_cells.json
    v
sections/section_00.json ... section_03.json + sections/table_cells.json
    |
    v
[4+1 parallel subagent tasks]
    |  4 agents: one per paragraph section (translated_00..03.json)
    |  1 agent: table cells (translated_table.json)
    |  Each agent self-validates before returning (see Subagent Instructions)
    |  Each writes a JSON file to sections/
    v
sections/translated_00.json ... translated_03.json + sections/translated_table.json
    |
    v
[python3 fix_json.py]
    |  Auto-discovers all translated_*.json files
    |  Repairs broken JSON from Georgian „" quotation marks
    v
[python3 stitch_docx.py]
    |  Opens source.docx, replaces paragraph + table cell text
    |  Preserves formatting (bold, italic, font, headings, alignment)
    |  Reconstructs \n as <w:br/> XML elements
    |  Auto-names output as next variant number
    v
[python3 verify_structure.py]
    |  Compares source.docx vs latest variant
    |  Checks: paragraph count, table count, heading styles,
    |  all 57 clause numbers, all 9 section headings,
    |  no dropped translations, terminology consistency,
    |  no Russian months, no forbidden terms
    |  Exits with code 1 if any errors found
    v
variant X.docx  (PASS = ready, FAIL = fix and re-run)
```

---

## Subagent Instructions

### Paragraph Section Agents (4 agents, parallel)

Each agent gets the content of `sections/section_XX.json` pasted into its prompt.
Each agent writes its result to `sections/translated_XX.json`.

**Prompt template** (paste glossary from "Mandatory Terminology Glossary" section at the end):

```
You are a Russian to Georgian translator. Translate the following Russian text
paragraphs to Georgian. This is a labor contract (შრომითი ხელშეკრულება) for a
barista-cashier position in Tbilisi.

RULES:
1. Keep proper names in Cyrillic (e.g. Иванова Анна, Петров Иван) — do NOT transliterate
2. Keep numbers, dates, IDs, phone numbers, addresses verbatim — NEVER modify factual data
3. Use ONLY the Georgian terms from the glossary below — no synonyms
4. Translate ALL text including month names (февраля -> თებერვალი)
5. Return ONLY a valid JSON array: [{"index": N, "text": "..."}]
6. Keep empty paragraphs as empty strings
7. Translate EVERYTHING — never summarize, omit, or simplify any clause
8. Do literal translation — don't adapt to Georgian law (keep notice periods, etc. as source says)
9. Use Unicode Georgian quotation marks „" (U+201E / U+201C), never ASCII "

SELF-VALIDATION — before returning, check:
- Every paragraph index from input appears in output (count must match)
- No Russian words remain (except proper names, IDs, numbers)
- All month names are in Georgian
- "Работник" = დასაქმებული everywhere (NEVER თანამშრომელი or მუშაკი)
- "Работодатель" = დამსაქმებელი everywhere
- "локальные нормативные акты" = შრომის შინაგანაწესი (NEVER ლოკალური ნორმატიული აქტები or ადგილობრივი — those are Russicisms)
- "Должностная инструкция" = სამუშაოს აღწერილობა (NEVER თანამდებობრივი ინსტრუქცია — that is a Russicism)
- "Неполная занятость / неполное рабочее время" = არასრული სამუშაო განაკვეთი (NOT არასრული დასაქმება)
- Clause numbers (1.1., 2.3., 8.1.2.) preserved exactly
- JSON is valid — test-parse it mentally, no unescaped quotes
- Fix any issue before returning

Here are the paragraphs to translate:
[PASTE SECTION JSON HERE]

GLOSSARY:
[PASTE FULL GLOSSARY HERE]

Write the result to sections/translated_XX.json
```

### Table Cell Agent (1 agent, parallel with paragraph agents)

Gets the content of `sections/table_cells.json`.
Writes result to `sections/translated_table.json`.

**Prompt template:**

```
You are a Russian to Georgian translator. Translate the following table cells
from a labor contract signature block.

RULES:
- Same rules as paragraph translation (see glossary)
- Keep proper names in Cyrillic, keep all numbers/IDs/phone numbers verbatim
- "Работодатель" = დამსაქმებელი, "Работник" = დასაქმებული
- Return JSON array: [{"key": "t_0_r0_c0", "text": "..."}, {"key": "t_0_r0_c1", "text": "..."}]
- Translate ALL Russian text including month names, labels (Подпись -> ხელმოწერა, Дата -> თარიღი, etc.)
- Preserve \n line breaks as-is

Here are the table cells:
[PASTE table_cells.json CONTENT HERE]

GLOSSARY (signature block terms):
Работодатель -> დამსაქმებელი
Работник -> დასაქმებული
Индивидуальный предприниматель -> ინდივიდუალური მეწარმე
Подпись -> ხელმოწერა
Дата -> თარიღი
номер паспорта -> პასპორტის ნომერი
моб. номер -> მობ. ნომერი
ID -> ID
февраля -> თებერვალი

Write the result to sections/translated_table.json
```

---

## Post-Stitch Structural Verification

After stitching the output DOCX, run a verification script that checks:

### Structure Check
Compare source and output document structure side by side:

1. **Paragraph count**: source paragraphs == output paragraphs (must be exactly 98)
2. **Table count**: source tables == output tables (must be 1)
3. **Table cell count**: source cells == output cells (must be 2)
4. **Heading styles preserved**: every paragraph that was Heading 1 / Heading 2 in source must still be Heading 1 / Heading 2 in output

### Section & Clause Completeness
Parse both source and output to extract all section/clause numbers and verify 1:1 mapping:

```
Expected structure:
  1. ПРЕДМЕТ ДОГОВОРА / ხელშეკრულების საგანი
    1.1. / 1.2. / 1.3.
  2. ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ / სამსახურებრივი მოვალეობები
    2.1. / 2.2. / 2.3. / 2.4. / 2.5.
  3. МЕСТО РАБОТЫ И СРОК ДЕЙСТВИЯ ДОГОВОРА / სამუშაო ადგილი და ხელშეკრულების მოქმედების ვადა
    3.1. / 3.2. / 3.3.
  4. РАБОЧЕЕ ВРЕМЯ / სამუშაო დრო
    4.1. / 4.2. / 4.3.
  5. ОПЛАТА ТРУДА / შრომის ანაზღაურება
    5.1. / 5.2. / 5.3. / 5.4. / 5.5.
  6. ПРАВА И ОБЯЗАННОСТИ РАБОТНИКА / დასაქმებულის უფლებები და მოვალეობები
    6.1. / 6.1.1. / 6.1.2. / 6.1.3. / 6.1.4.
    6.2. / 6.2.1. / 6.2.2. / 6.2.3. / 6.2.4. / 6.2.5. / 6.2.6. / 6.2.7. / 6.2.8.
  7. ПРАВА И ОБЯЗАННОСТИ РАБОТОДАТЕЛЯ / დამსაქმებლის უფლებები და მოვალეობები
    7.1. / 7.1.1. / 7.1.2. / 7.1.3. / 7.1.4.
    7.2. / 7.2.1. / 7.2.2. / 7.2.3. / 7.2.4. / 7.2.5. / 7.2.6. / 7.2.7. / 7.2.8. / 7.2.9.
  8. ПРЕКРАЩЕНИЕ ТРУДОВОГО ДОГОВОРА / შრომითი ხელშეკრულების შეწყვეტა
    8.1. / 8.1.1. / 8.1.2. / 8.1.3. / 8.1.4. / 8.2.
  9. ПОДПИСИ СТОРОН / მხარეთა ხელმოწერები
    9.1. / 9.2. / 9.3.
```

The verification must:
- Extract all clause numbers from source (regex: `^\d+\.\d+\.?(\d+\.?)?`)
- Extract all clause numbers from output
- Report any missing or extra clauses
- Report any clause number that changed

### Terminology Consistency Check
Grep the output for forbidden terms and required terms:

- **FAIL if found:** `თანამშრომელი`, `მუშაკი` (wrong terms for Employee)
- **FAIL if found:** `ლოკალური ნორმატიული აქტები`, `ადგილობრივი ნორმატიული აქტები` (Russicisms — should be `შინაგანაწესი`)
- **FAIL if found:** `თანამდებობრივი ინსტრუქცია` (Russicism — should be `სამუშაოს აღწერილობა`)
- **FAIL if found:** `არასრული დასაქმება` (should be `არასრული სამუშაო განაკვეთი`, Art. 16)
- **FAIL if found:** Any Russian month name (`января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря`) outside of proper names
- **PASS check:** `დასაქმებული` appears in output
- **PASS check:** `დამსაქმებელი` appears in output
- **PASS check:** `შინაგანაწესი` appears in output
- **PASS check:** `სამუშაოს აღწერილობა` appears in output

### Content Completeness Check
For each non-empty source paragraph, verify the corresponding output paragraph is also non-empty. Flag any paragraph where source has text but output is empty (= dropped translation).

---

## Findings from Previous Translation Sessions

Three sessions produced `variant 1.docx`, `variant 2.docx`, `variant 3.docx`. Comparing them revealed critical differences that inform the glossary and rules.

### Critical Differences Found

| # | Issue | Variant 1 | Variant 2 | Verdict |
|---|-------|-----------|-----------|---------|
| 1 | **"Работник" term** | Mixed: `თანამშრომელი` (Art.4-6), `მუშაკი` (Art.7), `დასაქმებული` (Art.8-9) | `დასაქმებული` consistently, except `თანამშრომელი` in para 94 | **Use `დასაქმებული` everywhere** — it is the Georgian Labor Code Art. 2 term |
| 2 | **Notice period (Art. 8.1.2)** | Changed to `30 კალენდარული დღე` (30 calendar days) per Georgian Labor Code Art. 48(3) | Kept literal `ორი კვირა` (2 weeks) from Russian source | **Translate literally** — this is a translation, not legal adaptation. However: Georgian Labor Code Art. 48(3) requires 30 calendar days for employee-initiated termination. The Russian "2 weeks" comes from Russian Labor Code Art. 80. **Flag this as a translator's note** (see Legal Warnings below) |
| 3 | **Para 33 month name** | `თებერვალი` (correct) | `февраля` left in Russian (defect) | **Must translate all month names** |
| 4 | **Employer address (para 2)** | Invented a different street and house number | Kept the source village name | **Never modify factual data** — translate, don't alter addresses |
| 5 | **Employee address (para 4)** | Invented a different house and flat number | Kept the source numbers | **Never modify factual data**. This is the failure mode to watch for: the model rewrites an address into something more plausible-looking, and nothing downstream catches it |
| 6 | **"Далее именуемый"** | `შემდგომში სახელწოდებით` ("hereinafter named") | `შემდგომში მოხსენიებული როგორც` ("hereinafter referred to as") | Both acceptable. `მოხსენიებული როგორც` is more standard in Georgian legal docs |
| 7 | **Quotation marks** | Proper Unicode `„"` (\u201e/\u201c) | `„"` but ASCII close-quote broke JSON | **Use Unicode `„"` (U+201E / U+201C)** — matches Georgian typographic convention |
| 8 | **"Посетитель"** | `ვიზიტორი` (visitor) | `მომხმარებელი` (customer/consumer) | `მომხმარებელი` is better for commercial/service context |
| 9 | **"Кофейня"** | `ყავის მაღაზია` (coffee shop) | `ყავის სახლი` (coffee house) | Web research confirms **`კაფე`** is the standard modern Georgian word. `ყავის მაღაზია` means a retail store selling coffee beans |
| 10 | **"Должностная инструкция"** | `თანამდებობრივი ინსტრუქცია` | `სამუშაო აღწერილობა` (job description) | **V2 was correct.** Web research confirms `სამუშაოს აღწერილობა` is from Labor Code Art. 14(1)(ე). `თანამდებობრივი ინსტრუქცია` is a Russicism |
| 11 | **"Локальные нормативные акты"** | `ლოკალური ნორმატიული აქტები` consistently | Mixed: `ლოკალური` and `ადგილობრივი` | **Both were wrong.** Web research confirms the Georgian Labor Code (Art. 23) uses **შრომის შინაგანაწესი** (internal labour regulations). Both `ლოკალური` and `ადგილობრივი` are Russicisms not found in the Code |
| 12 | **Para 15 (grind ratios)** | Simplified, omitted details about coffee per cup, tamping force, water pass time | Translated completely | **Translate everything** — do not summarize or omit |
| 13 | **Para 25 (coffee prep)** | Simplified, omitted alcohol/spices/ingredients detail | Translated completely | **Translate everything** |
| 14 | **Signature table** | Translated (t_0_r0_c0, t_0_r0_c1) | Not translated (missed entirely) | **Must translate table cells too** |
| 15 | **"Под подпись"** | Omitted or generalized | `ხელმოწერით` (with signature) | `ხელმოწერით` is correct |

### Rules Derived from Findings

1. **Never modify factual data** (addresses, names, numbers, dates, phone numbers)
2. **Translate everything** — never summarize, omit, or simplify
3. **Translate ALL month names** (февраля -> თებერვალი, etc.)
4. **Use consistent terminology** — one term per concept, everywhere
5. **Include table cells** in translation scope
6. **Use proper Unicode quotation marks** `„"` (U+201E/U+201C)
7. **Do literal translation** — don't change what the source says (keep notice periods, amounts, dates exactly as written)
8. **Use real Georgian Labor Code terms** — not Russicisms (see glossary). Confirmed via [matsne.gov.ge](https://matsne.gov.ge/ka/document/view/1155567)

---

## Legal Warnings

The source contract was drafted in Russian and contains terms/provisions from **Russian labor law** that conflict with **Georgian labor law**. Since this is a translation task (not legal adaptation), translate literally but be aware of these conflicts:

| Clause | Russian Source Says | Georgian Law Says | Action |
|--------|-------------------|-------------------|--------|
| Art. 8.1.2 — Notice period | 2 weeks (из российского ТК, ст. 80) | **30 calendar days** (Labor Code Art. 48(3)) | Translate literally as "ორი კვირა". The legal conflict is the drafter's problem |
| Art. 5.4 — Annual leave | 24 working days | **24 working days** (Art. 31) | No conflict — translate as-is |
| "Локальные нормативные акты" | Russian HR concept (multiple internal docs) | Georgian equivalent is **შინაგანაწესი** (single internal regulations document, Art. 23) | Use Georgian term. The concepts don't map 1:1 but შინაგანაწესი is the closest legal equivalent |

---

## Mandatory Terminology Glossary

**Source:** Georgian Labor Code (საქართველოს შრომის კოდექსი) via [matsne.gov.ge](https://matsne.gov.ge/ka/document/view/1155567). Verified Feb 2026.

Include this in every subagent prompt. These are the ONLY acceptable translations.

### Core Legal Terms

| Russian | Georgian | Source |
|---------|----------|--------|
| Трудовой договор | შრომითი ხელშეკრულება | Throughout the Code |
| Работодатель | დამსაქმებელი | Art. 3 — **the only legal term** |
| Работник | დასაქმებული | Art. 3 — **the only legal term. NEVER** `თანამშრომელი` (= colleague, not a legal term) **or** `მუშაკი` (= archaic Soviet-era term) |
| Стороны | მხარეები | |
| далее именуемый | შემდგომში მოხსენიებული როგორც | |
| Индивидуальный предприниматель | ინდივიდუალური მეწარმე | |
| Физическое лицо | ფიზიკური პირი | |
| Предмет договора | ხელშეკრულების საგანი | |
| Должностные обязанности | სამსახურებრივი მოვალეობები | |
| Заработная плата / зарплата | ხელფასი / შრომის ანაზღაურება | |
| Неполная занятость / неполное рабочее время | არასრული სამუშაო განაკვეთი | Art. 16 — the Code's term. NOT `არასრული დასაქმება` |
| Ставка (0,5 ставки) | განაკვეთი (0,5 განაკვეთი) | |
| Срочный договор | ვადიანი ხელშეკრულება | Art. 12 |
| Сверхурочная работа | ზეგანაკვეთური სამუშაო | Art. 27 |
| Ежегодный оплачиваемый отпуск | ანაზღაურებადი შვებულება | Art. 31 (min. 24 working days) |
| Выходные дни | დასვენების დღეები | |
| Праздничные дни | სადღესასწაულო დღეები | |
| Локальные нормативные акты | შრომის შინაგანაწესი | Art. 23 — **this is the real Georgian legal term** (= internal labour regulations). `ლოკალური ნორმატიული აქტები` is a Russicism not found in the Georgian Labor Code. `ადგილობრივი ნორმატიული აქტები` is also wrong |
| Должностная инструкция | სამუშაოს აღწერილობა | Art. 14(1)(ე) — the Code uses `შესასრულებელი სამუშაოს სახე და აღწერილობა`. `თანამდებობრივი ინსტრუქცია` is a Russicism |
| Охрана труда | შრომის დაცვა | |
| Техника безопасности | ტექნიკური უსაფრთხოება | |
| Пожарная безопасность | ხანძარსაწინააღმდეგო უსაფრთხოება | |
| Производственная санитария | საწარმოო სანიტარია | |
| Прекращение / расторжение договора | შრომითი ხელშეკრულების შეწყვეტა | Art. 47 |
| Увольнение | გათავისუფლება | |
| Соглашение сторон | მხარეთა შეთანხმება | |
| Внутренний распорядок | შინაგანაწესი | Art. 23 |
| Кассовая дисциплина | სალარო დისციპლინა | |
| Юридическая сила | იურიდიული ძალა | |
| Персональные данные | პერსონალური მონაცემები | |
| Под подпись / ознакомлен под подпись | ხელმოწერით / გაცნობილი ხელმოწერით | |
| Имущество | ქონება | |
| Распоряжения, указания, поручения | განკარგულებები, მითითებები, დავალებები | |
| Экземпляр | ეგზემპლარი | |

### Terms Previously Wrong in Variants 1-3

These were Russicisms (literal calques from Russian) that don't exist in the Georgian Labor Code:

| Wrong (Russicism) | Correct (Labor Code) | Why |
|---|---|---|
| `ლოკალური ნორმატიული აქტები` | **შრომის შინაგანაწესი** | Art. 23 defines internal regulations; the Russian concept "локальные нормативные акты" maps to this |
| `ადგილობრივი ნორმატიული აქტები` | **შრომის შინაგანაწესი** | Same — just a different wrong translation |
| `თანამდებობრივი ინსტრუქცია` | **სამუშაოს აღწერილობა** | Art. 14(1)(ე); "должностная инструкция" is a Russian HR concept, Georgian equivalent is "job description" |
| `თანამშრომელი` | **დასაქმებული** | თანამშრომელი means "colleague/coworker", not a legal term |
| `მუშაკი` | **დასაქმებული** | მუშაკი is archaic/Soviet-era, not in the current Code |
| `არასრული დასაქმება` | **არასრული სამუშაო განაკვეთი** | Art. 16 uses განაკვეთი (schedule/rate), not დასაქმება |

### Position-Specific Terms

| Russian | Georgian |
|---------|----------|
| Бариста | ბარისტა |
| Бариста-кассир | ბარისტა-მოლარე |
| Кофе-машина | ყავის აპარატი |
| Латте-арт | ლატე-არტი |
| Барная стойка | ბარის დახლი |
| Посетитель | მომხმარებელი |
| Кофейня | კაფე | (most natural modern Georgian; NOT `ყავის მაღაზია` which means coffee retail store, NOT `ყავის სახლი`) |
| Эспрессо, капучино, латте, ристретто, гляссе, американо | ესპრესო, კაპუჩინო, ლატე, რისტრეტო, გლასე, ამერიკანო |

### Month Names

| Russian | Georgian |
|---------|----------|
| января | იანვარი |
| февраля | თებერვალი |
| марта | მარტი |
| апреля | აპრილი |
| мая | მაისი |
| июня | ივნისი |
| июля | ივლისი |
| августа | აგვისტო |
| сентября | სექტემბერი |
| октября | ოქტომბერი |
| ноября | ნოემბერი |
| декабря | დეკემბერი |

### Date/Time Terms

| Russian | Georgian |
|---------|----------|
| года / год | წელი / წელს |
| день | დღე |
| неделя | კვირა |
| месяц | თვე |
| час | საათი |

### Signature Block Terms

| Russian | Georgian |
|---------|----------|
| Подпись | ხელმოწერა |
| Дата | თარიღი |
| номер паспорта | პასპორტის ნომერი |
| моб. номер | მობ. ნომერი |

---

## Technical Gotchas

### JSON Encoding
Georgian quotation marks `„"` (U+201E opening, U+201C closing) look like ASCII `"` but are different Unicode codepoints. When a subagent uses `"` (U+0022) as the closing Georgian quote inside a JSON string value, it breaks JSON parsing. Run `fix_json.py` after each translation to detect and repair.

### Line Breaks in Paragraphs
The source DOCX has multi-line paragraphs (addresses, signature blocks) using `<w:br/>` XML elements. `python-docx` serializes these as `\n` in `para.text`. When reconstructing the output DOCX, split translated text on `\n` and insert `<w:br/>` runs to preserve the visual line breaks.

### Table Cells
The source DOCX has a signature table at the bottom with 2 cells (employer / employee). `python-docx` exposes these via `doc.tables[0].rows[0].cells[0..1]`. These must be extracted, translated, and stitched back separately from paragraphs.

### Formatting Preservation
When replacing paragraph text, clone the first run's formatting (font, bold, italic, size) and apply to all new runs. Clear remaining original runs. This preserves the document's visual appearance.

---

## Where the findings came from

Three translation runs of the same source document produced three variants, and the
table above is what comparing them revealed. The variants and the source are not in this
repository — the glossary, the forbidden-term list and the verification script are the
distilled result, and they are what you actually need.

The useful habit: keep every variant, diff them against each other rather than reading
each in isolation. Four of the fifteen findings above are cases where two runs disagreed
and neither was obviously wrong on its own.

---

## Pre-Delivery Checklist

### Completeness
- [ ] All paragraphs translated (98/98)
- [ ] All table cells translated (2/2)
- [ ] All month names in Georgian (not Russian)
- [ ] No source paragraphs with text have empty translations

### Accuracy
- [ ] No addresses or factual data modified from source
- [ ] All clause numbers preserved (1.1., 2.3., 8.1.2., etc.)
- [ ] Literal translation — no legal adaptation (notice periods, etc.)

### Terminology Consistency
- [ ] `დასაქმებული` used everywhere for "Работник" (grep check)
- [ ] `დამსაქმებელი` used everywhere for "Работодатель" (grep check)
- [ ] `შინაგანაწესი` used for "локальные нормативные акты" (grep check)
- [ ] `სამუშაოს აღწერილობა` used for "Должностная инструкция"
- [ ] No `თანამშრომელი` or `მუშაკი` anywhere
- [ ] No `ლოკალური ნორმატიული აქტები` or `ადგილობრივი ნორმატიული` anywhere (Russicisms)
- [ ] No `თანამდებობრივი ინსტრუქცია` anywhere (Russicism)
- [ ] No `არასრული დასაქმება` anywhere (should be `არასრული სამუშაო განაკვეთი`)

### Structure
- [ ] 9 sections present in output (Art. 1-9)
- [ ] All sub-clauses present: 1.1-1.3, 2.1-2.5, 3.1-3.3, 4.1-4.3, 5.1-5.5, 6.1.1-6.1.4, 6.2.1-6.2.8, 7.1.1-7.1.4, 7.2.1-7.2.9, 8.1.1-8.1.4, 8.2, 9.1-9.3
- [ ] Heading styles preserved (Heading 1, Heading 2)
- [ ] Bulleted list items preserved (para 11-18, dash-prefixed knowledge requirements)

### Formatting
- [ ] Unicode quotation marks `„"` used (not ASCII `"` inside Georgian text)
- [ ] Line breaks preserved in multi-line paragraphs
- [ ] Output DOCX opens correctly in Word/LibreOffice
