#!/usr/bin/env python3
"""Convert structured extracted JSON to generated program-rule JSON.

Usage:
    python scripts/normalize_rules.py input.extracted.json output.generated.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def _parse_credit_text(text: str, pattern: str) -> int | None:
    """Extract credit count from text like '核心課程學分數：9 學分'.

    Args:
        text: The text to parse.
        pattern: Regex pattern with one capture group for the number.

    Returns:
        Extracted integer or None.
    """
    match = re.search(pattern, text)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            return None
    return None


def _generate_rule_id(program_id: str, category: str, index: int) -> str:
    """Generate a unique rule ID.

    Format: {program_id}_{category}_{index:03d}
    """
    return f"{program_id}_{category}_{index:03d}"


def _detect_star_tag(course_name: str, note: str, source_text: str) -> list[str]:
    """Detect if a course has the star (＊) tag.

    Checks courseName, note, and sourceText for the ＊ character.
    """
    if "＊" in course_name or "＊" in note or "＊" in source_text:
        return ["star"]
    return []


def normalize_rules(input_path: str, output_path: str) -> dict:
    """Convert structured extracted JSON to program-rule JSON.

    Args:
        input_path: Path to structured extracted JSON.
        output_path: Path to write generated program-rule JSON.

    Returns:
        Generated program-rule dict.
    """
    with open(input_path, "r", encoding="utf-8") as f:
        structured = json.load(f)

    # Derive program_id from programName
    program_name = structured.get("programName", "")
    # Simple slug generation for program_id
    id_map = {
        "軟體工程學程": "software_engineering",
        "日本研究學程": "japanese_studies",
        "金融工程學程": "financial_engineering",
    }
    program_id = id_map.get(program_name, "")
    if not program_id:
        # Fallback: slugify
        slug = re.sub(r"[^\w一-鿿]", "_", program_name).strip("_")
        slug = re.sub(r"_+", "_", slug)
        program_id = slug if slug else "unknown_program"

    version = structured.get("version", "")

    # Parse credit requirements
    core_credits = None
    total_credits = None

    core_text = structured.get("coreCreditText", "")
    total_text = structured.get("totalCreditText", "")

    if core_text:
        core_credits = _parse_credit_text(core_text, r"(\d+)\s*學分")
        if core_credits is None:
            core_credits = _parse_credit_text(core_text, r"(\d+)")

    if total_text:
        total_credits = _parse_credit_text(total_text, r"至少\s*(\d+)\s*學分")
        if total_credits is None:
            total_credits = _parse_credit_text(total_text, r"(\d+)\s*學分")
        if total_credits is None:
            total_credits = _parse_credit_text(total_text, r"(\d+)")

    # Build course rules
    course_rules = []
    core_idx = 0
    elective_idx = 0

    for row in structured.get("rows", []):
        section = row.get("section", "elective")
        category = "core" if section == "core" else "elective"

        course_name = row.get("courseName", "")
        equivalent_names = row.get("equivalentNames", [])
        credits = row.get("credits", 0)
        note = row.get("note", "")
        opening_units = row.get("openingUnits", [])
        source_text = row.get("sourceText", "")
        page = row.get("page", 1)

        # Merge courseName + equivalentNames into match.names
        names = [course_name] + equivalent_names

        # Determine offeringUnitMode and offeringUnits
        if opening_units:
            offering_unit_mode = "allow"
            offering_units = opening_units
        else:
            offering_unit_mode = "any"
            offering_units = []

        # Detect tags
        tags = _detect_star_tag(course_name, note, source_text)

        # Generate rule ID
        if category == "core":
            core_idx += 1
            rule_id = _generate_rule_id(program_id, "core", core_idx)
        else:
            elective_idx += 1
            rule_id = _generate_rule_id(program_id, "elective", elective_idx)

        # Clean course name (remove ＊ prefix for display)
        display_name = course_name.replace("＊", "").strip()

        course_rules.append({
            "ruleId": rule_id,
            "category": category,
            "displayName": display_name,
            "match": {
                "names": names,
                "offeringUnitMode": offering_unit_mode,
                "offeringUnits": offering_units,
            },
            "credits": credits,
            "groupId": None,
            "tags": tags,
            "priority": 100,
            "source": {
                "file": structured.get("sourceFile", ""),
                "page": page,
                "sourceText": source_text,
            },
        })

    # Build requirements
    requirements = {}
    if total_credits is not None:
        requirements["totalCredits"] = total_credits
    else:
        requirements["totalCredits"] = 0

    category_credits = []
    if core_credits is not None:
        category_credits.append({"category": "core", "credits": core_credits})
    requirements["categoryCredits"] = category_credits

    # Detect tag requirements from notes
    tag_credits = []
    notes = structured.get("notes", [])
    for note in notes:
        note_text = note.get("text", "")
        # Look for star credit requirements
        star_match = re.search(r"＊.*?至少\s*(\d+)\s*學分", note_text)
        if star_match:
            tag_credits.append({"tag": "star", "credits": int(star_match.group(1))})
    requirements["tagCredits"] = tag_credits

    # Build the final program-rule JSON
    result = {
        "programId": program_id,
        "programName": program_name,
        "version": version,
        "effectiveFrom": version,
        "requirements": requirements,
        "courseRules": course_rules,
        "groupRules": [],
        "prerequisites": [],
        "exemptionRules": [],
        "options": {
            "allowExtraCoreAsElective": False,
            "sameStudentCourseCanCountOnce": True,
        },
        "source": {
            "file": structured.get("sourceFile", ""),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        },
    }

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(course_rules)} course rules for {program_name}")
    print(f"Output saved to: {output_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Normalize structured extracted JSON to program-rule JSON")
    parser.add_argument("input", help="Input structured extracted JSON file path")
    parser.add_argument("output", help="Output generated program-rule JSON file path")
    args = parser.parse_args()

    normalize_rules(args.input, args.output)


if __name__ == "__main__":
    main()