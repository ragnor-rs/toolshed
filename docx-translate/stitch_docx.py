#!/usr/bin/env python3
"""Stitch translated sections into a new Georgian docx, preserving original formatting."""
import json
import os
import sys
import glob
import copy
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(PROJECT_DIR, "source.docx")
SECTIONS_DIR = os.path.join(PROJECT_DIR, "sections")

# Determine next variant number (parse actual numbers to avoid collisions)
existing = glob.glob(os.path.join(PROJECT_DIR, "variant *.docx"))
existing_nums = []
for path in existing:
    base = os.path.basename(path)
    try:
        num = int(base.replace("variant ", "").replace(".docx", ""))
        existing_nums.append(num)
    except ValueError:
        pass
next_num = max(existing_nums) + 1 if existing_nums else 1
OUTPUT_FILE = os.path.join(PROJECT_DIR, f"variant {next_num}.docx")

# Load all translated paragraph sections and merge
all_translations = {}
translated_files = sorted(f for f in glob.glob(os.path.join(SECTIONS_DIR, "translated_*.json")) if "translated_table" not in f)
if not translated_files:
    print("ERROR: No translated_*.json files found")
    sys.exit(1)

for path in translated_files:
    with open(path, "r", encoding="utf-8") as f:
        section = json.load(f)
    for item in section:
        all_translations[item["index"]] = item["text"]

print(f"Loaded {len(all_translations)} translated paragraphs from {len(translated_files)} files")

# Load translated table cells if available
table_translations = {}
table_translated_file = os.path.join(SECTIONS_DIR, "translated_table.json")
if os.path.exists(table_translated_file):
    with open(table_translated_file, "r", encoding="utf-8") as f:
        table_data = json.load(f)
    for item in table_data:
        table_translations[item["key"]] = item["text"]
    print(f"Loaded {len(table_translations)} translated table cells")
else:
    print("WARNING: No translated_table.json found — table cells will NOT be translated")

# Open original document
doc = Document(INPUT_FILE)


def replace_para_text(para, new_text):
    """Replace paragraph text preserving first run's formatting, handling line breaks."""
    if not new_text and not para.text.strip():
        return

    if not para.runs:
        para.text = new_text
        return

    first_run = para.runs[0]

    # Clear all existing runs
    for run in para.runs:
        run._element.getparent().remove(run._element)

    # Handle line breaks
    if "\\n" in new_text:
        parts = new_text.split("\\n")
    elif "\n" in new_text:
        parts = new_text.split("\n")
    else:
        parts = [new_text]

    for j, part in enumerate(parts):
        if j > 0:
            br_run = copy.deepcopy(first_run._element)
            for child in list(br_run):
                br_run.remove(child)
            rpr = first_run._element.find(qn("w:rPr"))
            if rpr is not None:
                br_run.insert(0, copy.deepcopy(rpr))
            etree.SubElement(br_run, qn("w:br"))
            para._element.append(br_run)

        new_run = copy.deepcopy(first_run._element)
        for child in list(new_run):
            new_run.remove(child)
        rpr = first_run._element.find(qn("w:rPr"))
        if rpr is not None:
            new_run.insert(0, copy.deepcopy(rpr))
        t_elem = etree.SubElement(new_run, qn("w:t"))
        t_elem.text = part
        t_elem.set(qn("xml:space"), "preserve")
        para._element.append(new_run)


# Replace paragraphs
for i, para in enumerate(doc.paragraphs):
    if i not in all_translations:
        print(f"WARNING: Missing translation for paragraph {i}: {para.text[:50]}")
        continue
    replace_para_text(para, all_translations[i])

# Replace table cells
for ti, table in enumerate(doc.tables):
    for ri, row in enumerate(table.rows):
        for ci, cell in enumerate(row.cells):
            key = f"t_{ti}_r{ri}_c{ci}"
            if key in table_translations:
                # Replace text in all paragraphs of the cell
                new_text = table_translations[key]
                # Remove all paragraphs except first to avoid ghost spacing
                for para in cell.paragraphs[1:]:
                    para._element.getparent().remove(para._element)
                # Set first paragraph
                if cell.paragraphs:
                    replace_para_text(cell.paragraphs[0], new_text)

# Save
doc.save(OUTPUT_FILE)
print(f"Translated document saved to: {OUTPUT_FILE}")
print(f"Total paragraphs: {len(doc.paragraphs)}")
