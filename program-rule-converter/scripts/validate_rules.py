#!/usr/bin/env python3
"""Validate program-rule JSON files against business rules.

Usage:
    python scripts/validate_rules.py final.json [--structured extracted.json]

Exit code 0 if all validations pass, 1 if any fail.
"""

import argparse
import json
import sys


def validate_rules(rule_path: str, structured_path: str | None = None) -> list[str]:
    """Validate a program-rule JSON against business rules.

    Args:
        rule_path: Path to the final program-rule JSON.
        structured_path: Optional path to structured extracted JSON for cross-checking.

    Returns:
        List of error messages. Empty list means all validations passed.
    """
    with open(rule_path, "r", encoding="utf-8") as f:
        rule = json.load(f)

    errors = []

    # 1. Required top-level fields
    for field in ["programId", "programName", "version"]:
        val = rule.get(field, "")
        if not val or not str(val).strip():
            errors.append(f"{field} 不可為空")

    # 2. requirements.totalCredits must exist and be positive
    requirements = rule.get("requirements", {})
    total_credits = requirements.get("totalCredits", 0)
    if not isinstance(total_credits, int) or total_credits <= 0:
        errors.append(f"requirements.totalCredits 必須是正整數，目前為 {total_credits}")

    # 3. totalCredits must not be less than core credits
    category_credits = requirements.get("categoryCredits", [])
    core_required = sum(
        cc["credits"] for cc in category_credits if cc.get("category") == "core"
    )
    if total_credits < core_required:
        errors.append(
            f"totalCredits ({total_credits}) 不得小於核心要求學分 ({core_required})"
        )

    # 4. courseRules must not be empty
    course_rules = rule.get("courseRules", [])
    if not course_rules:
        errors.append("courseRules 不可為空")

    # 5. Validate each courseRule
    seen_rule_ids = set()
    for i, cr in enumerate(course_rules, 1):
        prefix = f"courseRules[{i}]"

        # 5a. Required fields
        for field in ["ruleId", "category", "displayName", "credits", "source"]:
            if field not in cr or cr[field] is None:
                errors.append(f"{prefix}.{field} 不可為空")

        # 5b. match.names must exist and not be empty
        match = cr.get("match", {})
        names = match.get("names", [])
        if not names:
            errors.append(f"{prefix}.match.names 不可為空")

        # 5c. ruleId must be unique
        rule_id = cr.get("ruleId", "")
        if rule_id in seen_rule_ids:
            errors.append(f"{prefix}.ruleId 重複: {rule_id}")
        seen_rule_ids.add(rule_id)

        # 5d. credits must be reasonable integer (1-10)
        credits = cr.get("credits", 0)
        if not isinstance(credits, int) or credits < 1 or credits > 10:
            errors.append(f"{prefix}.credits 必須是 1-10 的整數，目前為 {credits}")

        # 5e. offeringUnitMode=allow must have non-empty offeringUnits
        if match.get("offeringUnitMode") == "allow":
            units = match.get("offeringUnits", [])
            if not units:
                errors.append(f"{prefix}.offeringUnitMode=allow 時 offeringUnits 不可為空")

        # 5f. source must have page and sourceText
        source = cr.get("source", {})
        if not source.get("page"):
            errors.append(f"{prefix}.source.page 不可為空")
        if not source.get("sourceText"):
            errors.append(f"{prefix}.source.sourceText 不可為空")

    # 6. Cross-check with structured data (if provided)
    if structured_path:
        with open(structured_path, "r", encoding="utf-8") as f:
            structured = json.load(f)

        # 6a. Check for JLPT exemptions
        notes_text = " ".join(
            n.get("text", "") for n in structured.get("notes", [])
        )
        has_jlpt_in_notes = "JLPT" in notes_text or "日本語能力試驗" in notes_text
        has_exemptions = len(rule.get("exemptionRules", [])) > 0
        if has_jlpt_in_notes and not has_exemptions:
            errors.append("備註提及 JLPT 但 exemptionRules 為空")

        # 6b. Check for ＊ tags
        for cr in course_rules:
            display_name = cr.get("displayName", "")
            source_text = cr.get("source", {}).get("sourceText", "")
            note = ""
            # Find matching row in structured data
            for row in structured.get("rows", []):
                if cr.get("displayName") in row.get("courseName", ""):
                    note = row.get("note", "")
                    break
            if "＊" in display_name or "＊" in source_text or "＊" in note:
                if "star" not in cr.get("tags", []):
                    errors.append(
                        f"courseRule '{display_name}' 的 sourceText/note 含 ＊ 但缺少 tags: ['star']"
                    )

    # 7. Check for prerequisite rules (specific to financial engineering)
    program_id = rule.get("programId", "")
    if "financial" in program_id.lower() or "金融" in rule.get("programName", ""):
        notes_text = ""
        if structured_path:
            with open(structured_path, "r", encoding="utf-8") as f:
                structured = json.load(f)
            notes_text = " ".join(
                n.get("text", "") for n in structured.get("notes", [])
            )
        if "微積分" in notes_text and not rule.get("prerequisites"):
            errors.append("金融工程學程備註提及微積分但 prerequisites 為空")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate program-rule JSON against business rules")
    parser.add_argument("rule", help="Path to final program-rule JSON file")
    parser.add_argument("--structured", help="Optional path to structured extracted JSON for cross-checking")
    args = parser.parse_args()

    errors = validate_rules(args.rule, args.structured)

    if errors:
        print(f"✗ Validation failed with {len(errors)} error(s):")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    else:
        print(f"✓ All business rules validated: {args.rule}")


if __name__ == "__main__":
    main()