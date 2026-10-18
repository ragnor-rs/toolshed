#!/usr/bin/env python3
"""Fix JSON files that have unescaped double quotes inside text values.

Handles both paragraph files (with "index" keys) and table files (with "key" keys).
Uses json_repair library if available, falls back to manual regex repair.
"""
import json
import glob
import os
import re
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SECTIONS_DIR = os.path.join(PROJECT_DIR, "sections")

# Try to use json_repair library for robust fixing
try:
    from json_repair import repair_json
    HAS_JSON_REPAIR = True
except ImportError:
    HAS_JSON_REPAIR = False


def _detect_format(content):
    """Detect whether the JSON uses 'index' (paragraphs) or 'key' (table cells)."""
    if '"index"' in content:
        return "index"
    if '"key"' in content:
        return "key"
    return None


def _fix_with_json_repair(content, filepath):
    """Use json_repair library for robust fixing."""
    repaired = repair_json(content, return_objects=True)
    if not isinstance(repaired, list):
        return None
    output = json.dumps(repaired, ensure_ascii=False, indent=2)
    json.loads(output)  # verify
    print(f"{os.path.basename(filepath)}: Fixed with json_repair! {len(repaired)} items")
    return output


def _fix_manual_index(content):
    """Manual repair for paragraph JSON files (with "index" key)."""
    results = []
    idx_pattern = re.compile(r'"index":\s*(\d+)')

    parts = content.split('{"index"')
    for part in parts[1:]:
        idx_match = idx_pattern.search('{"index"' + part)
        if not idx_match:
            continue
        index = int(idx_match.group(1))

        text_start = part.find('"text": "')
        if text_start == -1:
            text_start = part.find('"text":"')
            if text_start == -1:
                continue
            text_start += len('"text":"')
        else:
            text_start += len('"text": "')

        stripped = part.rstrip().rstrip(',').rstrip()
        if stripped.endswith('"}'):
            text_end = len(stripped) - 2
        else:
            text_end = stripped.rfind('"}')

        if text_end <= text_start:
            results.append({"index": index, "text": ""})
            continue

        text_value = part[text_start:text_end]
        # Escape unescaped ASCII double quotes inside text values
        text_value = text_value.replace('"', '\\"')

        results.append({"index": index, "text": text_value})

    results.sort(key=lambda x: x['index'])
    return results


def _fix_manual_key(content):
    """Manual repair for table JSON files (with "key" key)."""
    results = []
    key_pattern = re.compile(r'"key":\s*"([^"]+)"')

    parts = content.split('{"key"')
    for part in parts[1:]:
        key_match = key_pattern.search('{"key"' + part)
        if not key_match:
            continue
        key = key_match.group(1)

        text_start = part.find('"text": "')
        if text_start == -1:
            text_start = part.find('"text":"')
            if text_start == -1:
                continue
            text_start += len('"text":"')
        else:
            text_start += len('"text": "')

        stripped = part.rstrip().rstrip(',').rstrip()
        if stripped.endswith('"}'):
            text_end = len(stripped) - 2
        else:
            text_end = stripped.rfind('"}')

        if text_end <= text_start:
            results.append({"key": key, "text": ""})
            continue

        text_value = part[text_start:text_end]
        # Escape unescaped ASCII double quotes inside text values
        text_value = text_value.replace('"', '\\"')

        results.append({"key": key, "text": text_value})

    return results


def fix_json_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # First try parsing as-is
    try:
        data = json.loads(content)
        print(f"{os.path.basename(filepath)}: Already valid JSON ({len(data)} items)")
        return
    except json.JSONDecodeError:
        pass

    # Try json_repair library first (most robust)
    if HAS_JSON_REPAIR:
        try:
            output = _fix_with_json_repair(content, filepath)
            if output:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(output)
                return
        except Exception as e:
            print(f"{os.path.basename(filepath)}: json_repair failed ({e}), trying manual fix")

    # Manual repair — detect format
    fmt = _detect_format(content)
    if fmt == "index":
        results = _fix_manual_index(content)
    elif fmt == "key":
        results = _fix_manual_key(content)
    else:
        print(f"ERROR: {os.path.basename(filepath)}: Cannot detect format (no 'index' or 'key' found)")
        sys.exit(1)

    if not results:
        print(f"ERROR: {os.path.basename(filepath)}: Manual repair produced 0 items — refusing to overwrite")
        sys.exit(1)

    output = json.dumps(results, ensure_ascii=False, indent=2)
    json.loads(output)  # verify
    print(f"{os.path.basename(filepath)}: Fixed manually! {len(results)} items")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(output)


if __name__ == "__main__":
    # Auto-discover all translated_*.json files
    pattern = os.path.join(SECTIONS_DIR, "translated_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"No translated_*.json files found in {SECTIONS_DIR}")
        sys.exit(1)
    for f in files:
        fix_json_file(f)
