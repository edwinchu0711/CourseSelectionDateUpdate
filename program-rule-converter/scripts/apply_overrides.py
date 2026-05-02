#!/usr/bin/env python3
"""Apply manual overrides to generated program-rule JSON.

Usage:
    python scripts/apply_overrides.py generated.json override.json final.json
"""

import argparse
import json
import sys

from datetime import datetime, timezone


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override dict into base dict.

    For list values, override replaces base entirely.
    For dict values, recursively merge.
    For scalar values, override replaces base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_course_group_assignments(
    course_rules: list[dict],
    assignments: list[dict],
) -> list[dict]:
    """Apply courseGroupAssignments to course rules.

    For each assignment, find course rules whose displayName or match.names
    match any name in the assignment's names list, and set their groupId.
    """
    from src.evaluator.normalize import normalize_text

    for assignment in assignments:
        group_id = assignment["groupId"]
        target_names = assignment.get("names", [])

        # Normalize target names for matching
        normalized_targets = {normalize_text(n) for n in target_names}

        for rule in course_rules:
            # Check displayName
            if normalize_text(rule.get("displayName", "")) in normalized_targets:
                rule["groupId"] = group_id
                continue

            # Check match.names
            for name in rule.get("match", {}).get("names", []):
                if normalize_text(name) in normalized_targets:
                    rule["groupId"] = group_id
                    break

    return course_rules


def apply_overrides(generated_path: str, override_path: str, output_path: str) -> dict:
    """Apply manual overrides to a generated program-rule JSON.

    Args:
        generated_path: Path to generated program-rule JSON.
        override_path: Path to override JSON.
        output_path: Path to write final program-rule JSON.

    Returns:
        Final program-rule dict with overrides applied.
    """
    with open(generated_path, "r", encoding="utf-8") as f:
        generated = json.load(f)

    with open(override_path, "r", encoding="utf-8") as f:
        overrides = json.load(f)

    # Apply requirementsPatch
    if "requirementsPatch" in overrides:
        generated["requirements"] = _deep_merge(
            generated.get("requirements", {}),
            overrides["requirementsPatch"],
        )

    # Apply optionsPatch
    if "optionsPatch" in overrides:
        generated["options"] = _deep_merge(
            generated.get("options", {}),
            overrides["optionsPatch"],
        )

    # Apply groupRulesPatch — replace entirely
    if "groupRulesPatch" in overrides:
        existing_ids = {g["groupId"] for g in generated.get("groupRules", [])}
        for rule in overrides["groupRulesPatch"]:
            if rule["groupId"] not in existing_ids:
                generated.setdefault("groupRules", []).append(rule)
            else:
                # Update existing group rule
                for i, existing in enumerate(generated["groupRules"]):
                    if existing["groupId"] == rule["groupId"]:
                        generated["groupRules"][i] = rule
                        break

    # Apply exemptionRulesPatch — replace entirely
    if "exemptionRulesPatch" in overrides:
        generated["exemptionRules"] = overrides["exemptionRulesPatch"]

    # Apply prerequisitesPatch — replace entirely
    if "prerequisitesPatch" in overrides:
        generated["prerequisites"] = overrides["prerequisitesPatch"]

    # Apply courseGroupAssignments
    if "courseGroupAssignments" in overrides:
        generated["courseRules"] = _apply_course_group_assignments(
            generated.get("courseRules", []),
            overrides["courseGroupAssignments"],
        )

    # Update source timestamp
    generated["source"]["generatedAt"] = datetime.now(timezone.utc).isoformat()
    generated["source"]["overrideApplied"] = override_path

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)

    print(f"Applied overrides from {override_path}")
    print(f"Output saved to: {output_path}")

    return generated


def main():
    parser = argparse.ArgumentParser(description="Apply manual overrides to generated program-rule JSON")
    parser.add_argument("generated", help="Generated program-rule JSON file path")
    parser.add_argument("override", help="Override JSON file path")
    parser.add_argument("output", help="Output final program-rule JSON file path")
    args = parser.parse_args()

    apply_overrides(args.generated, args.override, args.output)


if __name__ == "__main__":
    main()