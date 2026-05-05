"""
Build programs.json manifest from rules/*.json files.

Scans the rules directory for program JSON files and generates a programs.json
manifest that the browser can fetch to discover available programs.

Usage:
    python build_manifest.py
"""

import json
from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
OUTPUT_PATH = Path(__file__).parent / "programs.json"


def build_manifest():
    programs = []

    if not RULES_DIR.exists():
        print(f"Rules directory not found: {RULES_DIR}")
        return

    for f in sorted(RULES_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)

            years = sorted(set(v["academic_year"] for v in data.get("versions", [])))
            programs.append({
                "id": data["program_id"],
                "name": data["program_name"],
                "name_en": data.get("program_name_en"),
                "type": data.get("program_type", ""),
                "years": years,
                "is_discontinued": data.get("is_discontinued", False),
                "filename": f.name,
            })
        except Exception as e:
            print(f"Warning: Failed to process {f.name}: {e}")

    manifest = {"programs": programs}

    with open(OUTPUT_PATH, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)

    print(f"Generated manifest with {len(programs)} programs → {OUTPUT_PATH}")
    for p in programs:
        print(f"  {p['id']}: {p['name']} ({p['type']}) — years: {p['years']}")


if __name__ == "__main__":
    build_manifest()