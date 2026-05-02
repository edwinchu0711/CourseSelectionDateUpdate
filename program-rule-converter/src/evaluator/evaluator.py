"""Program rule evaluator — determines if a student meets program requirements."""

from __future__ import annotations

from datetime import datetime, timezone

from .matcher import match_student_course
from .normalize import normalize_text


def evaluate_program(
    student_profile: dict,
    student_courses: list[dict],
    program_rule: dict,
) -> dict:
    """Evaluate whether a student meets program requirements.

    Args:
        student_profile: Student profile with studentId, department, etc.
        student_courses: List of student course records.
        program_rule: Program rule JSON (final format).

    Returns:
        Evaluation result dict with passed, summary, missing, acceptedCourses,
        rejectedCourses, and warnings.
    """
    requirements = program_rule.get("requirements", {})
    course_rules = program_rule.get("courseRules", [])
    group_rules = program_rule.get("groupRules", [])
    prerequisites = program_rule.get("prerequisites", [])
    exemption_rules = program_rule.get("exemptionRules", [])
    options = program_rule.get("options", {})

    allow_extra_core_as_elective = options.get("allowExtraCoreAsElective", False)
    same_course_count_once = options.get("sameStudentCourseCanCountOnce", True)

    # Step 1: Match all student courses to rules
    accepted_courses = []
    rejected_courses = []

    # Track which student courses have been matched to which rules
    # Key: (courseId, ruleId) to handle sameStudentCourseCanCountOnce
    matched_pairs: dict[tuple[str, str], dict] = {}

    # Build group tracking: groupId -> list of accepted course entries in that group
    group_assignments: dict[str, list[dict]] = {g["groupId"]: [] for g in group_rules}
    # Build group max map
    group_max: dict[str, int] = {g["groupId"]: g.get("maxAcceptedCourses", 1) for g in group_rules}
    # Build group overflow map
    group_overflow: dict[str, str | None] = {
        g["groupId"]: g.get("overflowCategory") for g in group_rules
    }

    # Track which student courses have been used (for sameStudentCourseCanCountOnce)
    used_student_courses: set[str] = set()

    for course in student_courses:
        if not course.get("passed", False):
            continue

        matches = match_student_course(course, course_rules)
        if not matches:
            continue

        course_id = course.get("courseId", course.get("courseName", ""))

        for rule in matches:
            rule_id = rule["ruleId"]
            pair_key = (course_id, rule_id)

            if same_course_count_once and course_id in used_student_courses:
                rejected_courses.append({
                    "courseName": course.get("courseName", ""),
                    "reason": f"同一課程已採認於其他規則",
                })
                continue

            group_id = rule.get("groupId")
            category = rule["category"]

            # Check group constraints
            if group_id and group_id in group_assignments:
                current_count = len(group_assignments[group_id])
                max_count = group_max.get(group_id, 1)

                if current_count >= max_count:
                    overflow_cat = group_overflow.get(group_id)
                    if overflow_cat and allow_extra_core_as_elective:
                        # Overflow to alternative category
                        category = overflow_cat
                    else:
                        rejected_courses.append({
                            "courseName": course.get("courseName", ""),
                            "reason": f"同群組 {group_id} 已採認其他課程",
                        })
                        continue

            entry = {
                "courseName": course.get("courseName", ""),
                "acceptedAs": category,
                "creditsCounted": course.get("credits", 0),
                "matchedRuleId": rule_id,
            }

            if group_id and group_id in group_assignments:
                group_assignments[group_id].append(entry)

            accepted_courses.append(entry)
            used_student_courses.add(course_id)

    # Step 2: Handle exemption rules (certificate-based credit grants)
    exemption_credits = {"core": 0, "elective": 0}
    student_certs = student_profile.get("certificates", [])
    for ex_rule in exemption_rules:
        if ex_rule.get("type") == "certificate":
            cert_type = ex_rule.get("certificateType", "")
            cert_levels = ex_rule.get("levels", [])

            for cert in student_certs:
                if cert.get("type") == cert_type and cert.get("level") in cert_levels:
                    grant = ex_rule.get("grant", {})
                    cat = grant.get("category", "core")
                    credits = grant.get("credits", 0)
                    exemption_credits[cat] += credits

    # Step 3: Check prerequisites
    prerequisite_warnings = []
    for prereq in prerequisites:
        if prereq.get("type") == "passed_course":
            required_names = prereq.get("names", [])
            min_credits = prereq.get("minCredits", 0)
            passed = student_profile.get("passedPrerequisites", [])

            found = False
            for name in required_names:
                for p in passed:
                    if normalize_text(p.get("courseName", "")) == normalize_text(name):
                        if p.get("passed", False) and p.get("credits", 0) >= min_credits:
                            found = True
                            break
                if found:
                    break

            if not found:
                prerequisite_warnings.append(
                    f"未滿足先修課程要求: {', '.join(required_names)} ({prereq.get('note', '')})"
                )

    # Step 4: Calculate credit totals
    category_credits = {"core": 0, "elective": 0}
    tag_credits: dict[str, int] = {}

    # Add matched course credits
    for entry in accepted_courses:
        cat = entry["acceptedAs"]
        credits = entry["creditsCounted"]
        category_credits[cat] = category_credits.get(cat, 0) + credits

    # Find tag credits for matched rules
    for entry in accepted_courses:
        rule_id = entry["matchedRuleId"]
        for rule in course_rules:
            if rule["ruleId"] == rule_id:
                for tag in rule.get("tags", []):
                    tag_credits[tag] = tag_credits.get(tag, 0) + entry["creditsCounted"]

    # Add exemption credits
    for cat, creds in exemption_credits.items():
        category_credits[cat] = category_credits.get(cat, 0) + creds

    # Apply allowExtraCoreAsElective
    total_credits = sum(category_credits.values())

    required_core = sum(
        cc["credits"] for cc in requirements.get("categoryCredits", [])
        if cc["category"] == "core"
    )
    actual_core = category_credits.get("core", 0)

    if allow_extra_core_as_elective and actual_core > required_core:
        overflow = actual_core - required_core
        # Extra core credits count toward elective requirement but
        # total stays the same since we already sum both categories
        pass

    # Calculate outside credits
    outside_credits = 0
    outside_req = requirements.get("outsideCredits", {})
    if outside_req:
        student_dept = student_profile.get("department", "")
        exclude_own = outside_req.get("excludeStudentUnits", False)

        for entry in accepted_courses:
            rule_id = entry["matchedRuleId"]
            for rule in course_rules:
                if rule["ruleId"] == rule_id:
                    # Find the matching student course to get offeringUnit
                    for course in student_courses:
                        if course.get("passed", False) and normalize_text(course.get("courseName", "")) == normalize_text(rule["displayName"]):
                            unit = course.get("offeringUnit", "")
                            if exclude_own and unit == student_dept:
                                continue
                            if unit != student_dept:
                                outside_credits += entry["creditsCounted"]
                            break

    # Step 5: Determine if passed
    required_total = requirements.get("totalCredits", 0)
    missing = []

    if total_credits < required_total:
        missing.append(f"總學分尚缺 {required_total - total_credits} 學分")

    for cc in requirements.get("categoryCredits", []):
        cat = cc["category"]
        req = cc["credits"]
        actual = category_credits.get(cat, 0)
        if actual < req:
            missing.append(f"{cat} 課程尚缺 {req - actual} 學分")

    for tc in requirements.get("tagCredits", []):
        tag = tc["tag"]
        req = tc["credits"]
        actual = tag_credits.get(tag, 0)
        if actual < req:
            missing.append(f"{tag} 課程尚缺 {req - actual} 學分")

    if outside_req:
        req_outside = outside_req.get("credits", outside_req.get("defaultCredits", 0))
        if outside_credits < req_outside:
            missing.append(f"外系學分尚缺 {req_outside - outside_credits} 學分")

    for w in prerequisite_warnings:
        missing.append(w)

    passed = len(missing) == 0

    return {
        "passed": passed,
        "programId": program_rule.get("programId", ""),
        "programName": program_rule.get("programName", ""),
        "version": program_rule.get("version", ""),
        "summary": {
            "totalCredits": total_credits,
            "requiredTotalCredits": required_total,
            "categoryCredits": category_credits,
            "tagCredits": tag_credits,
            "outsideCredits": outside_credits,
        },
        "missing": missing,
        "acceptedCourses": accepted_courses,
        "rejectedCourses": rejected_courses,
        "warnings": prerequisite_warnings,
    }