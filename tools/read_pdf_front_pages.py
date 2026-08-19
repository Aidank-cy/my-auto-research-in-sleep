#!/usr/bin/env python3
"""Extract page-scoped text from a PDF.

This is a minimal progressive-reading helper. It does not summarize or infer
paper fields; it only emits page-scoped text for downstream node extraction.

Examples
--------
python3 tools/read_pdf_front_pages.py paper.pdf
python3 tools/read_pdf_front_pages.py paper.pdf --pages 2 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def clean_page_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def extract_pages(pdf_path: Path, page_count: int | None = 2) -> dict[str, Any]:
    if page_count is not None and page_count < 1:
        raise ValueError("page_count must be >= 1")
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"not a PDF file: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    limit = len(reader.pages) if page_count is None else min(page_count, len(reader.pages))
    pages: list[dict[str, Any]] = []
    for index in range(limit):
        text = clean_page_text(reader.pages[index].extract_text() or "")
        pages.append(
            {
                "page": index + 1,
                "text": text,
                "char_count": len(text),
            }
        )
    return {
        "pdf_path": str(pdf_path),
        "requested_pages": page_count,
        "document_pages": len(reader.pages),
        "pages_read": limit,
        "pages": pages,
    }


def extract_front_pages(pdf_path: Path, page_count: int = 2) -> dict[str, Any]:
    return extract_pages(pdf_path, page_count=page_count)


def extract_fulltext(pdf_path: Path) -> dict[str, Any]:
    return extract_pages(pdf_path, page_count=None)


def render_text(payload: dict[str, Any]) -> str:
    chunks = [
        f"PDF: {payload['pdf_path']}",
        f"Pages read: {payload['pages_read']} / {payload['document_pages']}",
    ]
    for page in payload["pages"]:
        chunks.append(f"\n--- Page {page['page']} ---\n{page['text']}")
    return "\n".join(chunks).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", type=Path, help="PDF file path.")
    parser.add_argument("--pages", type=int, default=2, help="Number of front pages to extract (default: 2).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of page-marked text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = extract_front_pages(args.pdf, page_count=args.pages)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
