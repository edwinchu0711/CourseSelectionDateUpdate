#!/usr/bin/env python3
"""Validate JSON files against JSON Schema.

Usage:
    python scripts/validate_schema.py schema.json data.json

Exit code 0 if validation passes, 1 if it fails.
"""

import argparse
import json
import sys


def validate_schema(schema_path: str, data_path: str) -> bool:
    """Validate a JSON data file against a JSON Schema.

    Args:
        schema_path: Path to the JSON Schema file.
        data_path: Path to the JSON data file to validate.

    Returns:
        True if validation passes, False otherwise.
    """
    import jsonschema

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        jsonschema.validate(data, schema)
        print(f"✓ Validation passed: {data_path}")
        return True
    except jsonschema.ValidationError as e:
        print(f"✗ Validation failed: {data_path}")
        print(f"  Path: {'/'.join(str(p) for p in e.absolute_path)}")
        print(f"  Error: {e.message}")
        return False
    except jsonschema.SchemaError as e:
        print(f"✗ Invalid schema: {schema_path}")
        print(f"  Error: {e.message}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Validate JSON data against JSON Schema")
    parser.add_argument("schema", help="Path to JSON Schema file")
    parser.add_argument("data", help="Path to JSON data file to validate")
    args = parser.parse_args()

    passed = validate_schema(args.schema, args.data)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()