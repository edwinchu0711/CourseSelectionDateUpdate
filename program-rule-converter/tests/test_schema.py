"""Tests for JSON Schema validation."""

import json
import os

import jsonschema
import pytest

SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas")


@pytest.fixture
def program_rule_schema():
    with open(os.path.join(SCHEMAS_DIR, "program-rule.schema.json"), "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def raw_extracted_schema():
    with open(os.path.join(SCHEMAS_DIR, "raw-extracted.schema.json"), "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def structured_extracted_schema():
    with open(os.path.join(SCHEMAS_DIR, "structured-extracted.schema.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class TestProgramRuleSchema:
    def test_valid_minimal(self, program_rule_schema):
        data = {
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
                    "tags": [],
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
        jsonschema.validate(data, program_rule_schema)  # Should not raise

    def test_missing_programId(self, program_rule_schema):
        data = {
            "programName": "test",
            "version": "1",
            "effectiveFrom": "1",
            "requirements": {"totalCredits": 10},
            "courseRules": [],
            "groupRules": [],
            "prerequisites": [],
            "exemptionRules": [],
            "options": {},
            "source": {"file": "test.pdf", "generatedAt": "2026-01-01"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, program_rule_schema)

    def test_invalid_category(self, program_rule_schema):
        data = {
            "programId": "test",
            "programName": "test",
            "version": "1",
            "effectiveFrom": "1",
            "requirements": {"totalCredits": 10},
            "courseRules": [
                {
                    "ruleId": "test_001",
                    "category": "invalid_category",
                    "displayName": "test",
                    "match": {"names": ["test"], "offeringUnitMode": "any", "offeringUnits": []},
                    "credits": 3,
                    "tags": [],
                    "source": {"file": "test.pdf", "page": 1, "sourceText": "test"},
                }
            ],
            "groupRules": [],
            "prerequisites": [],
            "exemptionRules": [],
            "options": {},
            "source": {"file": "test.pdf", "generatedAt": "2026-01-01"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, program_rule_schema)

    def test_credits_out_of_range(self, program_rule_schema):
        data = {
            "programId": "test",
            "programName": "test",
            "version": "1",
            "effectiveFrom": "1",
            "requirements": {"totalCredits": 10},
            "courseRules": [
                {
                    "ruleId": "test_001",
                    "category": "core",
                    "displayName": "test",
                    "match": {"names": ["test"], "offeringUnitMode": "any", "offeringUnits": []},
                    "credits": 15,  # exceeds maximum
                    "tags": [],
                    "source": {"file": "test.pdf", "page": 1, "sourceText": "test"},
                }
            ],
            "groupRules": [],
            "prerequisites": [],
            "exemptionRules": [],
            "options": {},
            "source": {"file": "test.pdf", "generatedAt": "2026-01-01"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, program_rule_schema)


class TestRawExtractedSchema:
    def test_valid(self, raw_extracted_schema):
        data = {
            "sourceFile": "test.pdf",
            "pages": [
                {"page": 1, "text": "Some text"},
                {"page": 2, "text": "More text"},
            ],
        }
        jsonschema.validate(data, raw_extracted_schema)

    def test_missing_pages(self, raw_extracted_schema):
        data = {"sourceFile": "test.pdf"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, raw_extracted_schema)


class TestStructuredExtractedSchema:
    def test_valid(self, structured_extracted_schema):
        data = {
            "sourceFile": "test.pdf",
            "programName": "軟體工程學程",
            "version": "114-1",
            "rows": [
                {
                    "section": "core",
                    "courseName": "計算機概論（一）",
                    "credits": 3,
                    "page": 1,
                    "sourceText": "資工系 計算機概論（一） 3",
                }
            ],
        }
        jsonschema.validate(data, structured_extracted_schema)

    def test_invalid_section(self, structured_extracted_schema):
        data = {
            "sourceFile": "test.pdf",
            "programName": "test",
            "version": "1",
            "rows": [
                {
                    "section": "invalid",
                    "courseName": "test",
                    "credits": 3,
                    "page": 1,
                    "sourceText": "test",
                }
            ],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, structured_extracted_schema)