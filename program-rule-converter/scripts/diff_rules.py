#!/usr/bin/env python3
"""Generate diff report between approved and final program-rule JSON files.

Usage:
    python scripts/diff_rules.py approved.json final.json diff-report.md
"""

import argparse
import json
import sys


def _course_key(course: dict) -> str:
    """Create a comparison key for a course rule."""
    return course.get("ruleId", "") or course.get("displayName", "")


def _compare_course(old: dict, new: dict) -> dict | None:
    """Compare two course rules and return differences, or None if identical."""
    changes = {}

    for field in ["category", "displayName", "credits", "groupId", "priority"]:
        old_val = old.get(field)
        new_val = new.get(field)
        if old_val != new_val:
            changes[field] = {"old": old_val, "new": new_val}

    # Compare match
    old_match = old.get("match", {})
    new_match = new.get("match", {})
    if old_match != new_match:
        changes["match"] = {"old": old_match, "new": new_match}

    # Compare tags
    old_tags = sorted(old.get("tags", []))
    new_tags = sorted(new.get("tags", []))
    if old_tags != new_tags:
        changes["tags"] = {"old": old_tags, "new": new_tags}

    return changes if changes else None


def diff_rules(approved_path: str, final_path: str, report_path: str) -> dict:
    """Generate a diff report between approved and final program-rule JSON.

    Args:
        approved_path: Path to the approved (baseline) program-rule JSON.
        final_path: Path to the final (new) program-rule JSON.
        report_path: Path to write the Markdown diff report.

    Returns:
        Dict with added, removed, changed, requirement_changes, warnings.
    """
    with open(approved_path, "r", encoding="utf-8") as f:
        approved = json.load(f)

    with open(final_path, "r", encoding="utf-8") as f:
        final = json.load(f)

    # Compare course rules
    approved_courses = {_course_key(c): c for c in approved.get("courseRules", [])}
    final_courses = {_course_key(c): c for c in final.get("courseRules", [])}

    approved_keys = set(approved_courses.keys())
    final_keys = set(final_courses.keys())

    added = final_keys - approved_keys
    removed = approved_keys - final_keys
    common = approved_keys & final_keys

    changed_courses = []
    for key in common:
        diffs = _compare_course(approved_courses[key], final_courses[key])
        if diffs:
            changed_courses.append({
                "ruleId": key,
                "displayName": final_courses[key].get("displayName", ""),
                "changes": diffs,
            })

    # Compare requirements
    requirement_changes = []
    approved_req = approved.get("requirements", {})
    final_req = final.get("requirements", {})

    if approved_req.get("totalCredits") != final_req.get("totalCredits"):
        requirement_changes.append(
            f"totalCredits: {approved_req.get('totalCredits')} → {final_req.get('totalCredits')}"
        )

    # Compare categoryCredits
    approved_cats = {cc["category"]: cc["credits"] for cc in approved_req.get("categoryCredits", [])}
    final_cats = {cc["category"]: cc["credits"] for cc in final_req.get("categoryCredits", [])}
    all_cats = set(approved_cats.keys()) | set(final_cats.keys())
    for cat in all_cats:
        old_val = approved_cats.get(cat, 0)
        new_val = final_cats.get(cat, 0)
        if old_val != new_val:
            requirement_changes.append(f"categoryCredits.{cat}: {old_val} → {new_val}")

    # Compare tagCredits
    approved_tags = {tc["tag"]: tc["credits"] for tc in approved_req.get("tagCredits", [])}
    final_tags = {tc["tag"]: tc["credits"] for tc in final_req.get("tagCredits", [])}
    all_tags = set(approved_tags.keys()) | set(final_tags.keys())
    for tag in all_tags:
        old_val = approved_tags.get(tag, 0)
        new_val = final_tags.get(tag, 0)
        if old_val != new_val:
            requirement_changes.append(f"tagCredits.{tag}: {old_val} → {new_val}")

    # Compare groupRules, exemptionRules, prerequisites
    warnings = []
    if approved.get("groupRules") != final.get("groupRules"):
        warnings.append("groupRules 有變更")
    if approved.get("exemptionRules") != final.get("exemptionRules"):
        warnings.append("exemptionRules 有變更")
    if approved.get("prerequisites") != final.get("prerequisites"):
        warnings.append("prerequisites 有變更")

    # Generate Markdown report
    program_id = final.get("programId", "unknown")
    version = final.get("version", "unknown")

    lines = [
        f"# Program Rule Diff Report",
        f"",
        f"## {program_id} {version}",
        f"",
        f"### Added Courses",
    ]

    if added:
        for key in sorted(added):
            lines.append(f"- {final_courses[key].get('displayName', key)}")
    else:
        lines.append("- None")

    lines.extend([
        f"",
        f"### Removed Courses",
    ])

    if removed:
        for key in sorted(removed):
            lines.append(f"- {approved_courses[key].get('displayName', key)}")
    else:
        lines.append("- None")

    lines.extend([
        f"",
        f"### Changed Courses",
    ])

    if changed_courses:
        for cc in changed_courses:
            lines.append(f"- **{cc['displayName']}** ({cc['ruleId']})")
            for field, change in cc["changes"].items():
                lines.append(f"  - {field}: {change['old']} → {change['new']}")
    else:
        lines.append("- None")

    lines.extend([
        f"",
        f"### Requirement Changes",
    ])

    if requirement_changes:
        for rc in requirement_changes:
            lines.append(f"- {rc}")
    else:
        lines.append("- None")

    lines.extend([
        f"",
        f"### Warnings",
    ])

    if warnings:
        for w in warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- None")

    # Write report
    report_content = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"Diff report written to: {report_path}")

    return {
        "added": list(added),
        "removed": list(removed),
        "changed": changed_courses,
        "requirement_changes": requirement_changes,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate diff report between approved and final program-rule JSON")
    parser.add_argument("approved", help="Path to approved (baseline) program-rule JSON")
    parser.add_argument("final", help="Path to final (new) program-rule JSON")
    parser.add_argument("report", help="Path to write Markdown diff report")
    args = parser.parse_args()

    diff_rules(args.approved, args.final, args.report)


if __name__ == "__main__":
    main()