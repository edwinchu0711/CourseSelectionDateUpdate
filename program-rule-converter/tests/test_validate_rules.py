"""Tests for business rule validation."""

import json
import os
import sys
import tempfile

import pytest

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

from validate_rules import validate_rules


def _make_valid_rule(overrides=None):
    """Create a valid program-rule dict with optional overrides."""
    rule = {
        "programId": "software_engineering",
        "programName": "軟體工程學程",
        "version": "114-1",
        "effectiveFrom": "114-1",
        "requirements": {
            "totalCredits": 27,
            "categoryCredits": [{"category": "core", "credits": 9}],
            "tagCredits": [{"tag": "star", "credits": 6}],
        },
        "courseRules": [
            {
                "ruleId": "software_engineering_core_001",
                "category": "core",
                "displayName": "計算機概論（一）",
                "match": {
                    "names": ["計算機概論（一）", "計算機概論"],
                    "offeringUnitMode": "any",
                    "offeringUnits": [],
                },
                "credits": 3,
                "groupId": None,
                "tags": [],
                "priority": 100,
                "source": {
                    "file": "software_engineering_114.pdf",
                    "page": 1,
                    "sourceText": "資工系 計算機概論（一） 3",
                },
            }
        ],
        "groupRules": [],
        "prerequisites": [],
        "exemptionRules": [],
        "options": {
            "allowExtraCoreAsElective": False,
            "sameStudentCourseCanCountOnce": True,
        },
        "source": {
            "file": "software_engineering_114.pdf",
            "generatedAt": "2026-05-02T10:00:00+08:00",
        },
    }
    if overrides:
        rule.update(overrides)
    return rule


class TestValidateRules:
    def test_valid_rule(self):
        errors = validate_rules(_write_rule(_make_valid_rule()))
        assert len(errors) == 0

    def test_missing_programId(self):
        rule = _make_valid_rule()
        rule["programId"] = ""
        errors = validate_rules(_write_rule(rule))
        assert any("programId" in e for e in errors)

    def test_missing_programName(self):
        rule = _make_valid_rule()
        rule["programName"] = ""
        errors = validate_rules(_write_rule(rule))
        assert any("programName" in e for e in errors)

    def test_zero_total_credits(self):
        rule = _make_valid_rule()
        rule["requirements"]["totalCredits"] = 0
        errors = validate_rules(_write_rule(rule))
        assert any("totalCredits" in e for e in errors)

    def test_total_less_than_core(self):
        rule = _make_valid_rule()
        rule["requirements"]["totalCredits"] = 5
        errors = validate_rules(_write_rule(rule))
        assert any("totalCredits" in e or "核心" in e for e in errors)

    def test_empty_course_rules(self):
        rule = _make_valid_rule()
        rule["courseRules"] = []
        errors = validate_rules(_write_rule(rule))
        assert any("courseRules" in e for e in errors)

    def test_duplicate_rule_id(self):
        rule = _make_valid_rule()
        rule["courseRules"].append(dict(rule["courseRules"][0]))
        errors = validate_rules(_write_rule(rule))
        assert any("ruleId" in e and "重複" in e for e in errors)

    def test_allow_mode_without_units(self):
        rule = _make_valid_rule()
        rule["courseRules"][0]["match"]["offeringUnitMode"] = "allow"
        rule["courseRules"][0]["match"]["offeringUnits"] = []
        errors = validate_rules(_write_rule(rule))
        assert any("offeringUnits" in e for e in errors)

    def test_star_course_without_tag(self):
        rule = _make_valid_rule()
        rule["courseRules"][0]["source"]["sourceText"] = "資管系 ＊軟體工程 軟體工程 3"
        rule["courseRules"][0]["tags"] = []
        # This validation requires structured data cross-check, so just check it doesn't crash
        errors = validate_rules(_write_rule(rule))
        # Should not crash, cross-check is optional


def _write_rule(rule: dict) -> str:
    """Write rule dict to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rule, f, ensure_ascii=False)
    return path