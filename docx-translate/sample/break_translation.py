#!/usr/bin/env python3
"""Inject known defects into a translation, to prove verify_structure.py catches them.

A verifier nobody has seen fail is a verifier nobody should trust. This damages
`sections/translated_*.json` in specific, documented ways, each aimed at one check in
`verify_structure.py`. Re-stitch and re-verify afterwards and it must exit 1, naming
every defect:

    python3 sample/sample_translation.py   # start from a clean translation
    python3 sample/break_translation.py    # damage it
    python3 stitch_docx.py
    python3 verify_structure.py            # must exit 1 and name all four
    python3 sample/sample_translation.py   # undo

Sample document only — it edits by paragraph index, so point it at nothing else.
"""
import argparse
import glob
import json
import os
import sys

PROJECT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
SECTIONS_DIR = os.path.join(PROJECT_DIR, "sections")

# Each defect: paragraph index -> (replacement text, which check should fire).
# The forbidden term must appear in its exact nominative form: the check is a literal
# substring match, so a declined form ("თანამდებობრივ ინსტრუქციას") slips past it.
DEFECTS = {
    "forbidden-term": (
        17,
        "2.3. დასაქმებული იცავს თანამდებობრივი ინსტრუქცია.",
        "Forbidden term found (Russicism for 'job description')",
    ),
    "russian-month": (
        1,
        "ქალაქი თბილისი, საქართველო\n „_01_“__марта___ 2026 წელი",
        "Untranslated Russian month found",
    ),
    "dropped-paragraph": (
        24,
        "",
        "Dropped translation (source non-empty, output empty)",
    ),
    "renumbered-clause": (
        31,
        "5.3. ხელშეკრულება შედგენილია ორ ეგზემპლარად, რომლებსაც აქვს თანაბარი იურიდიული ძალა.",
        "Missing clause 5.2. / extra clause 5.3.",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--defect", choices=sorted(DEFECTS), action="append",
                        help="inject only this defect (repeatable; default: all four)")
    args = parser.parse_args()

    chosen = args.defect or sorted(DEFECTS)

    # Build index -> text across every section file, so this works whatever the split.
    by_index = {}
    for path in sorted(glob.glob(os.path.join(SECTIONS_DIR, "translated_*.json"))):
        if "translated_table" in path:
            continue
        with open(path, "r", encoding="utf-8") as f:
            section = json.load(f)
        for pos, item in enumerate(section):
            by_index[item["index"]] = (path, pos)

    if not by_index:
        print("ERROR: no translated_*.json found — run sample/sample_translation.py first")
        sys.exit(1)

    edits = {}
    for name in chosen:
        index, text, expected = DEFECTS[name]
        if index not in by_index:
            print(f"ERROR: paragraph {index} not in the translation — "
                  f"this script only knows the synthetic sample")
            sys.exit(1)
        path, pos = by_index[index]
        edits.setdefault(path, []).append((pos, text))
        print(f"  para {index:2d}  {name:18s} -> expect: {expected}")

    for path, changes in edits.items():
        with open(path, "r", encoding="utf-8") as f:
            section = json.load(f)
        for pos, text in changes:
            section[pos]["text"] = text
        with open(path, "w", encoding="utf-8") as f:
            json.dump(section, f, ensure_ascii=False, indent=2)
        print(f"wrote {os.path.basename(path)}")

    print(f"\n{len(chosen)} defect(s) injected. Now:")
    print("  python3 stitch_docx.py && python3 verify_structure.py")
    print("verify_structure.py must exit 1. If it exits 0, the verifier is not working.")


if __name__ == "__main__":
    main()
