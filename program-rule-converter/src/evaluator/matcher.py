"""Course matching logic for student course records against program rules."""

from .normalize import normalize_text


def match_student_course(
    student_course: dict,
    course_rules: list[dict],
) -> list[dict]:
    """Match a student's course against all applicable course rules.

    Only matches passed courses. Uses normalized courseName for matching
    against rule match.names, and checks offeringUnitMode constraints.

    Args:
        student_course: A single student course record with fields:
            courseName, offeringUnit, credits, passed, etc.
        course_rules: List of course rules from program-rule JSON.

    Returns:
        List of matched course rules.
    """
    if not student_course.get("passed", False):
        return []

    normalized_name = normalize_text(student_course.get("courseName", ""))
    if not normalized_name:
        return []

    offering_unit = student_course.get("offeringUnit", "")
    matches = []

    for rule in course_rules:
        match_config = rule.get("match", {})
        rule_names = match_config.get("names", [])

        # Check if student course name matches any name in the rule
        name_matched = any(
            normalize_text(name) == normalized_name
            for name in rule_names
        )

        if not name_matched:
            continue

        # Check offering unit mode
        unit_mode = match_config.get("offeringUnitMode", "any")
        allowed_units = match_config.get("offeringUnits", [])

        if unit_mode == "any":
            matches.append(rule)
        elif unit_mode == "allow":
            if offering_unit in allowed_units:
                matches.append(rule)
        elif unit_mode == "deny":
            if offering_unit not in allowed_units:
                matches.append(rule)

    return matches