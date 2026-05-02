#!/usr/bin/env python3
"""Convert raw extracted JSON to structured extracted JSON using LLM.

Usage:
    python scripts/convert_with_llm.py input.raw.json output.extracted.json
"""

import argparse
import json
import os
import sys
import jsonschema


LLM_SYSTEM_PROMPT = """你是一個學分學程 PDF 課程表資料抽取器。

只能根據輸入文字抽取資料。
不得新增、推測或改寫課程名稱。
不得把備註誤當成課程。
每一筆課程都必須包含 page 與 sourceText。
credits 必須是整數。
section 只能是 core 或 elective。
如果欄位不存在，使用空字串、null 或空陣列。
輸出必須是 JSON。

你必須輸出以下 JSON 格式：
{
  "sourceFile": "來源檔名",
  "programName": "學程名稱",
  "version": "版本（例如 114-1）",
  "approvedDates": ["核可日期字串"],
  "coreCreditText": "核心課程學分數描述（例如：核心課程學分數：9 學分）",
  "totalCreditText": "總學分數描述（例如：總學分數：至少 27 學分）",
  "rows": [
    {
      "section": "core 或 elective",
      "openingUnits": ["開課單位"],
      "courseName": "課程名稱",
      "equivalentNames": ["別名1", "別名2"],
      "credits": 3,
      "note": "備註",
      "page": 1,
      "sourceText": "原始文字"
    }
  ],
  "notes": [
    {
      "page": 1,
      "text": "備註原始文字"
    }
  ]
}"""


def convert_with_llm(
    input_path: str,
    output_path: str,
    max_retries: int = 2,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Convert raw extracted JSON to structured format using OpenAI-compatible API.

    Args:
        input_path: Path to raw extracted JSON.
        output_path: Path to write structured extracted JSON.
        max_retries: Maximum number of retries on invalid JSON output.
        model: Model name (env LLM_MODEL, default: gpt-4o).
        base_url: API base URL (env LLM_BASE_URL, default: OpenAI).

    Returns:
        Structured extracted data dict.

    Raises:
        SystemExit: If all retries fail or API key is missing.
    """
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: LLM_API_KEY or OPENAI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    model = model or os.environ.get("LLM_MODEL") or "z-ai/glm-5.1"
    base_url = base_url or os.environ.get("LLM_BASE_URL") or "https://integrate.api.nvidia.com/v1"

    from openai import OpenAI

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    print(f"Using model: {model}, base_url: {base_url or 'OpenAI default'}")

    # Load raw extracted data
    with open(input_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Build the user message from raw pages
    pages_text = ""
    for page in raw_data.get("pages", []):
        pages_text += f"\n--- 第 {page['page']} 頁 ---\n{page['text']}\n"

    user_message = f"來源檔案: {raw_data.get('sourceFile', '')}\n\n以下是從 PDF 抽取的文字：{pages_text}"

    # Load schema for validation
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "schemas",
        "structured-extracted.schema.json",
    )

    result = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)

            # Validate against schema if available
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as sf:
                    schema = json.load(sf)
                jsonschema.validate(result, schema)

            # Validate that each row has page and sourceText
            for row in result.get("rows", []):
                if "page" not in row or "sourceText" not in row:
                    raise ValueError(f"Row missing page or sourceText: {row}")

            # Validation passed
            break

        except json.JSONDecodeError as e:
            last_error = e
            print(f"Attempt {attempt + 1}: Invalid JSON output - {e}", file=sys.stderr)
        except jsonschema.ValidationError as e:
            last_error = e
            print(f"Attempt {attempt + 1}: Schema validation failed - {e.message}", file=sys.stderr)
        except ValueError as e:
            last_error = e
            print(f"Attempt {attempt + 1}: Validation error - {e}", file=sys.stderr)
            result = None
        except Exception as e:
            last_error = e
            print(f"Attempt {attempt + 1}: Error - {e}", file=sys.stderr)

    if result is None:
        print(f"Error: Failed after {max_retries + 1} attempts. Last error: {last_error}", file=sys.stderr)
        sys.exit(1)

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(result.get('rows', []))} course rows from {input_path}")
    print(f"Output saved to: {output_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Convert raw extracted JSON to structured format using LLM")
    parser.add_argument("input", help="Input raw extracted JSON file path")
    parser.add_argument("output", help="Output structured extracted JSON file path")
    parser.add_argument("--max-retries", type=int, default=2, help="Maximum retries on invalid output")
    parser.add_argument("--model", default=None, help="LLM model name (default: env LLM_MODEL or z-ai/glm-5.1)")
    parser.add_argument("--base-url", default=None, help="API base URL (default: env LLM_BASE_URL or OpenAI default)")
    args = parser.parse_args()

    convert_with_llm(args.input, args.output, args.max_retries, model=args.model, base_url=args.base_url)


if __name__ == "__main__":
    main()