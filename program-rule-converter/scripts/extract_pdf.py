#!/usr/bin/env python3
"""Extract text from PDF files into raw JSON format.

Usage:
    python scripts/extract_pdf.py input.pdf output.raw.json
"""

import argparse
import json
import sys


def extract_pdf(input_path: str) -> dict:
    """Extract text from a PDF file, page by page.

    Uses PyMuPDF (fitz) as primary extractor.
    Falls back to pdfplumber if PyMuPDF is unavailable.

    Args:
        input_path: Path to the PDF file.

    Returns:
        Dict with sourceFile and pages containing extracted text.
    """
    import os

    source_file = os.path.basename(input_path)
    pages = []

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(input_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            pages.append({
                "page": page_num + 1,
                "text": text,
            })
        doc.close()
    except ImportError:
        print("PyMuPDF not available, falling back to pdfplumber...", file=sys.stderr)
        import pdfplumber

        with pdfplumber.open(input_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages.append({
                    "page": page_num + 1,
                    "text": text,
                })

    return {
        "sourceFile": source_file,
        "pages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract text from PDF into raw JSON")
    parser.add_argument("input", help="Input PDF file path")
    parser.add_argument("output", help="Output raw JSON file path")
    args = parser.parse_args()

    result = extract_pdf(args.input)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Extracted {len(result['pages'])} pages from {result['sourceFile']}")
    print(f"Output saved to: {args.output}")


if __name__ == "__main__":
    main()