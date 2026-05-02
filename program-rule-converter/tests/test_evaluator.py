"""Tests for the evaluator module."""

import pytest

from src.evaluator.normalize import normalize_text
from src.evaluator.matcher import match_student_course
from src.evaluator.evaluator import evaluate_program


# ── Fixtures ──────────────────────────────────────────────────────────────────

SOFTWARE_ENGINEERING_RULE = {
    "programId": "software_engineering",
    "programName": "軟體工程學程",
    "version": "114-1",
    "effectiveFrom": "114-1",
    "requirements": {
        "totalCredits": 27,
        "categoryCredits": [{"category": "core", "credits": 9}],
        "tagCredits": [{"tag": "star", "credits": 6}],
        "outsideCredits": {
            "credits": 6,
            "excludeStudentUnits": True,
        },
    },
    "courseRules": [
        {
            "ruleId": "se_core_001",
            "category": "core",
            "displayName": "計算機概論（一）",
            "match": {
                "names": ["計算機概論（一）", "計算機概論", "C 程式設計（一）"],
                "offeringUnitMode": "any",
                "offeringUnits": [],
            },
            "credits": 3,
            "groupId": None,
            "tags": [],
            "priority": 100,
            "source": {"file": "se.pdf", "page": 1, "sourceText": "test"},
        },
        {
            "ruleId": "se_core_002",
            "category": "core",
            "displayName": "資料結構",
            "match": {
                "names": ["資料結構"],
                "offeringUnitMode": "any",
                "offeringUnits": [],
            },
            "credits": 3,
            "groupId": None,
            "tags": [],
            "priority": 100,
            "source": {"file": "se.pdf", "page": 1, "sourceText": "test"},
        },
        {
            "ruleId": "se_core_003",
            "category": "core",
            "displayName": "作業系統",
            "match": {
                "names": ["作業系統"],
                "offeringUnitMode": "any",
                "offeringUnits": [],
            },
            "credits": 3,
            "groupId": None,
            "tags": [],
            "priority": 100,
            "source": {"file": "se.pdf", "page": 1, "sourceText": "test"},
        },
        {
            "ruleId": "se_elect_001",
            "category": "elective",
            "displayName": "軟體工程",
            "match": {
                "names": ["軟體工程"],
                "offeringUnitMode": "allow",
                "offeringUnits": ["資管系"],
            },
            "credits": 3,
            "groupId": None,
            "tags": ["star"],
            "priority": 100,
            "source": {"file": "se.pdf", "page": 1, "sourceText": "＊軟體工程"},
        },
    ],
    "groupRules": [],
    "prerequisites": [],
    "exemptionRules": [],
    "options": {
        "allowExtraCoreAsElective": False,
        "sameStudentCourseCanCountOnce": True,
    },
    "source": {"file": "se.pdf", "generatedAt": "2026-01-01"},
}


# ── normalize_text tests ──────────────────────────────────────────────────────

class TestNormalizeText:
    def test_strip_whitespace(self):
        assert normalize_text("  hello  ") == "hello"

    def test_fullwidth_parentheses(self):
        assert normalize_text("計算機概論（一）") == "計算機概論(一)"

    def test_fullwidth_letters(self):
        assert normalize_text("Ｃ語言") == "c語言"

    def test_fullwidth_digits(self):
        assert normalize_text("９８７") == "987"

    def test_fullwidth_star(self):
        assert normalize_text("＊軟體工程") == "*軟體工程"

    def test_fullwidth_space(self):
        assert normalize_text("軟　體") == "軟體"

    def test_remove_spaces(self):
        assert normalize_text("C 程式設計", remove_spaces=True) == "c程式設計"

    def test_lowercase(self):
        assert normalize_text("ABC") == "abc"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_combined(self):
        result = normalize_text("　Ｃ程式設計（一）", remove_spaces=True)
        assert result == "c程式設計(一)"


# ── matcher tests ────────────────────────────────────────────────────────────

class TestMatchStudentCourse:
    def test_match_by_name(self):
        course = {
            "courseName": "計算機概論",
            "offeringUnit": "資工系",
            "credits": 3,
            "passed": True,
        }
        matches = match_student_course(course, SOFTWARE_ENGINEERING_RULE["courseRules"])
        assert len(matches) == 1
        assert matches[0]["ruleId"] == "se_core_001"

    def test_match_equivalent_name(self):
        course = {
            "courseName": "C 程式設計（一）",
            "offeringUnit": "資工系",
            "credits": 3,
            "passed": True,
        }
        matches = match_student_course(course, SOFTWARE_ENGINEERING_RULE["courseRules"])
        assert len(matches) == 1
        assert matches[0]["ruleId"] == "se_core_001"

    def test_no_match_failed_course(self):
        course = {
            "courseName": "計算機概論",
            "offeringUnit": "資工系",
            "credits": 3,
            "passed": False,
        }
        matches = match_student_course(course, SOFTWARE_ENGINEERING_RULE["courseRules"])
        assert len(matches) == 0

    def test_allow_mode_matching(self):
        course = {
            "courseName": "軟體工程",
            "offeringUnit": "資管系",
            "credits": 3,
            "passed": True,
        }
        matches = match_student_course(course, SOFTWARE_ENGINEERING_RULE["courseRules"])
        assert len(matches) == 1
        assert matches[0]["ruleId"] == "se_elect_001"

    def test_allow_mode_denied(self):
        course = {
            "courseName": "軟體工程",
            "offeringUnit": "外語系",
            "credits": 3,
            "passed": True,
        }
        matches = match_student_course(course, SOFTWARE_ENGINEERING_RULE["courseRules"])
        assert len(matches) == 0


# ── evaluator tests ───────────────────────────────────────────────────────────

class TestEvaluateProgram:
    def test_passing_student(self):
        profile = {
            "studentId": "B123456789",
            "department": "資工系",
            "doubleMajors": [],
            "minors": [],
        }
        courses = [
            {"courseId": "CS101", "courseName": "計算機概論（一）", "offeringUnit": "資工系", "credits": 3, "term": "112-1", "passed": True},
            {"courseId": "CS201", "courseName": "資料結構", "offeringUnit": "資工系", "credits": 3, "term": "112-2", "passed": True},
            {"courseId": "CS301", "courseName": "作業系統", "offeringUnit": "資工系", "credits": 3, "term": "113-1", "passed": True},
            {"courseId": "MIS201", "courseName": "軟體工程", "offeringUnit": "資管系", "credits": 3, "term": "113-1", "passed": True},
        ]
        result = evaluate_program(profile, courses, SOFTWARE_ENGINEERING_RULE)
        assert result["programId"] == "software_engineering"
        assert result["summary"]["categoryCredits"]["core"] == 9
        assert result["summary"]["tagCredits"]["star"] == 3

    def test_failed_course_not_counted(self):
        profile = {"studentId": "B999", "department": "資工系"}
        courses = [
            {"courseId": "CS101", "courseName": "計算機概論（一）", "offeringUnit": "資工系", "credits": 3, "term": "112-1", "passed": False},
        ]
        result = evaluate_program(profile, courses, SOFTWARE_ENGINEERING_RULE)
        assert result["passed"] is False
        assert result["summary"]["totalCredits"] == 0

    def test_missing_credits(self):
        profile = {"studentId": "B999", "department": "資工系"}
        courses = [
            {"courseId": "CS101", "courseName": "計算機概論（一）", "offeringUnit": "資工系", "credits": 3, "term": "112-1", "passed": True},
        ]
        result = evaluate_program(profile, courses, SOFTWARE_ENGINEERING_RULE)
        assert result["passed"] is False
        assert any("總學分" in m for m in result["missing"])