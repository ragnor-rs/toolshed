#!/usr/bin/env python3
"""Stand-in for the LLM translation step, for the synthetic sample only.

The real translation step is not a script — it is a prompt plus a glossary, run as
several parallel agents (see TRANSLATION_GUIDE.md). That makes the documented pipeline
impossible to run end to end without an API key, so this script fills the gap for the
one document it knows: the synthetic contract written by `make_sample.py`.

It is a fixed lookup table, not a translator. It matches on source text, and refuses to
run if it sees a paragraph it does not recognise — if you dropped your own source.docx
beside the scripts, this script cannot help you and will say so.

The translations follow the glossary in TRANSLATION_GUIDE.md, so the output passes
`verify_structure.py`: დასაქმებული for Работник, შინაგანაწესი for внутренний распорядок,
სამუშაოს აღწერილობა for должностная инструкция, Georgian month names, and Unicode „"
quotation marks.

    python3 sample/sample_translation.py   # sections/ -> translated_*.json
"""
import glob
import json
import os
import sys

PROJECT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
SECTIONS_DIR = os.path.join(PROJECT_DIR, "sections")

# Source text -> Georgian. Keyed by source text rather than paragraph index so this
# survives any change to how extract_sections.py splits sections.
PARAGRAPHS = {
    "ТРУДОВОЙ ДОГОВОР № _001_":
        "შრომითი ხელშეკრულება № _001_",

    "город Тбилиси, Грузия\n «_01_»__марта___ 2026 года":
        "ქალაქი თბილისი, საქართველო\n „_01_“__მარტი___ 2026 წელი",

    "С одной стороны, Индивидуальный предприниматель _Иванова Анна_____\n"
    "Идентификационный номер: __000000000_____________________________\n"
    "Адрес регистрации: __Грузия, Тбилиси, ул. Примерная, д. 1________":
        "ერთი მხრივ, ინდივიდუალური მეწარმე _Иванова Анна_____\n"
        "იდენტიფიკაციის ნომერი: __000000000_____________________________\n"
        "რეგისტრაციის მისამართი: __საქართველო, თბილისი, ქუჩა Примерная, სახლი 1________",

    "далее именуемый «Работодатель» , а с другой стороны,":
        "შემდგომში მოხსენიებული როგორც „დამსაქმებელი“ , ხოლო მეორე მხრივ,",

    "физическое лицо____Петров  Иван________________________________\n"
    "Паспорт/ID: ___000000000_______________________________________\n"
    "Адрес: ___Тбилиси, ул. Примерная, д. 2, кв. 2__________________":
        "ფიზიკური პირი____Петров  Иван________________________________\n"
        "პასპორტი/ID: ___000000000_______________________________________\n"
        "მისამართი: ___თბილისი, ქუჩა Примерная, სახლი 2, ბინა 2__________________",

    "далее именуемый «Работник», заключили настоящий договор.":
        "შემდგომში მოხსენიებული როგორც „დასაქმებული“, დადეს წინამდებარე ხელშეკრულება.",

    "1. ПРЕДМЕТ ДОГОВОРА":
        "1. ხელშეკრულების საგანი",
    "1.1. Работодатель принимает Работника на должность бариста-кассира.":
        "1.1. დამსაქმებელი იღებს დასაქმებულს ბარისტა-მოლარის თანამდებობაზე.",
    "1.2. Работа по настоящему договору является для Работника основной.":
        "1.2. წინამდებარე ხელშეკრულებით გათვალისწინებული სამუშაო დასაქმებულისთვის ძირითადია.",
    "1.3. Договор заключается на определённый срок.":
        "1.3. ხელშეკრულება იდება განსაზღვრული ვადით.",

    "2. ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ":
        "2. სამსახურებრივი მოვალეობები",
    "2.1. Работник обязан знать и соблюдать следующее:":
        "2.1. დასაქმებული ვალდებულია იცოდეს და დაიცვას შემდეგი:",
    "- устройство и настройку кофе-машины;":
        "- ყავის აპარატის მოწყობილობა და გამართვა;",
    "- рецептуру эспрессо, капучино, латте, американо;":
        "- ესპრესოს, კაპუჩინოს, ლატეს, ამერიკანოს რეცეპტურა;",
    "- правила кассовой дисциплины;":
        "- სალარო დისციპლინის წესები;",
    "2.2. Работник обслуживает посетителей за барной стойкой.":
        "2.2. დასაქმებული ემსახურება მომხმარებლებს ბარის დახლთან.",
    "2.3. Работник соблюдает должностную инструкцию.":
        "2.3. დასაქმებული იცავს სამუშაოს აღწერილობას.",

    "3. РАБОЧЕЕ ВРЕМЯ":
        "3. სამუშაო დრო",
    "3.1. Работнику устанавливается неполное рабочее время — 0,5 ставки.":
        "3.1. დასაქმებულს განესაზღვრება არასრული სამუშაო განაკვეთი — 0,5 განაკვეთი.",
    "3.2. Выходные дни предоставляются согласно внутреннему распорядку.":
        "3.2. დასვენების დღეები მიეცემა შინაგანაწესის შესაბამისად.",

    "4. ОПЛАТА ТРУДА":
        "4. შრომის ანაზღაურება",
    "4.1. Заработная плата выплачивается два раза в месяц.":
        "4.1. ხელფასი გაიცემა თვეში ორჯერ.",
    "4.2. Работнику предоставляется ежегодный оплачиваемый отпуск 24 рабочих дня.":
        "4.2. დასაქმებულს მიეცემა წლიური ანაზღაურებადი შვებულება 24 სამუშაო დღე.",

    "5. ПРЕКРАЩЕНИЕ ТРУДОВОГО ДОГОВОРА":
        "5. შრომითი ხელშეკრულების შეწყვეტა",
    "5.1. Договор может быть прекращён по соглашению сторон.":
        "5.1. ხელშეკრულება შეიძლება შეწყდეს მხარეთა შეთანხმებით.",
    # Literal "two weeks", per TRANSLATION_GUIDE.md — the Georgian Labor Code's own
    # 30-day notice period is the drafter's problem, not the translator's.
    "5.1.1. Работник предупреждает Работодателя за две недели.":
        "5.1.1. დასაქმებული აფრთხილებს დამსაქმებელს ორი კვირით ადრე.",
    "5.1.2. Работодатель знакомит Работника с приказом под подпись.":
        "5.1.2. დამსაქმებელი აცნობს დასაქმებულს ბრძანებას ხელმოწერით.",
    "5.2. Договор составлен в двух экземплярах, имеющих равную юридическую силу.":
        "5.2. ხელშეკრულება შედგენილია ორ ეგზემპლარად, რომლებსაც აქვს თანაბარი იურიდიული ძალა.",
}

TABLE_CELLS = {
    "Работодатель\nИндивидуальный предприниматель\nИванова Анна\nID 000000000\n"
    "моб. номер +995 000 00 00 00\nПодпись ____________\nДата ____________":
        "დამსაქმებელი\nინდივიდუალური მეწარმე\nИванова Анна\nID 000000000\n"
        "მობ. ნომერი +995 000 00 00 00\nხელმოწერა ____________\nთარიღი ____________",

    "Работник\nПетров Иван\nномер паспорта 000000000\n"
    "моб. номер +995 000 00 00 00\nПодпись ____________\nДата ____________":
        "დასაქმებული\nПетров Иван\nპასპორტის ნომერი 000000000\n"
        "მობ. ნომერი +995 000 00 00 00\nხელმოწერა ____________\nთარიღი ____________",
}


def unknown(kind, text):
    print(f"ERROR: this is the sample stand-in, not a translator — it only knows the "
          f"document written by sample/make_sample.py.")
    print(f"       Unrecognised {kind}: {text[:70]!r}")
    print(f"       For your own source.docx, run the real translation step: the agent "
          f"prompts and glossary are in TRANSLATION_GUIDE.md.")
    sys.exit(1)


def main():
    section_files = sorted(glob.glob(os.path.join(SECTIONS_DIR, "section_*.json")))
    table_file = os.path.join(SECTIONS_DIR, "table_cells.json")
    if not section_files:
        print(f"ERROR: no section_*.json in {SECTIONS_DIR} — run extract_sections.py first")
        sys.exit(1)

    for path in section_files:
        with open(path, "r", encoding="utf-8") as f:
            section = json.load(f)

        translated = []
        for item in section:
            source = item["text"]
            if not source.strip():
                translated.append({"index": item["index"], "text": ""})
                continue
            if source not in PARAGRAPHS:
                unknown("paragraph", source)
            translated.append({"index": item["index"], "text": PARAGRAPHS[source]})

        out_path = os.path.join(
            SECTIONS_DIR,
            os.path.basename(path).replace("section_", "translated_"),
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(translated, f, ensure_ascii=False, indent=2)
        print(f"{os.path.basename(out_path)}: {len(translated)} paragraphs")

    if not os.path.exists(table_file):
        print(f"WARNING: no table_cells.json — skipping table")
        return

    with open(table_file, "r", encoding="utf-8") as f:
        cells = json.load(f)

    translated_cells = []
    for cell in cells:
        source = cell["text"]
        if not source.strip():
            translated_cells.append({"key": cell["key"], "text": ""})
            continue
        if source not in TABLE_CELLS:
            unknown("table cell", source)
        translated_cells.append({"key": cell["key"], "text": TABLE_CELLS[source]})

    out_path = os.path.join(SECTIONS_DIR, "translated_table.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(translated_cells, f, ensure_ascii=False, indent=2)
    print(f"translated_table.json: {len(translated_cells)} cells")


if __name__ == "__main__":
    main()
