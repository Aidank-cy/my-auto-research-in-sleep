#!/usr/bin/env python3
"""List PDF sections and extract one exact section with pypdf.

The tool is intentionally narrow: it uses PDF outline/bookmarks when present,
falls back to lightweight text heading detection, and never summarizes.

Examples
--------
python3 tools/research_pdf_section_extract.py paper.pdf --list-sections
python3 tools/research_pdf_section_extract.py paper.pdf --section "Introduction" --json
python3 tools/research_pdf_section_extract.py --topic-name agent-memory --paper-id p3 --list-sections
python3 tools/research_pdf_section_extract.py --topic-name agent-memory --paper-id p3 --section "Methodology"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_artifact_io import write_json  # noqa: E402
from research_packet_io import load_packet_by_id  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402


KNOWN_HEADINGS = {
    "abstract",
    "introduction",
    "background",
    "related work",
    "preliminaries",
    "method",
    "methods",
    "methodology",
    "approach",
    "model",
    "framework",
    "experiments",
    "experiment",
    "experimental setup",
    "evaluation",
    "results",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
    "references",
    "appendix",
    "acknowledgments",
}
NUMBERED_HEADING_RE = re.compile(
    r"^\s*((?:[0-9]+(?:\.[0-9]+)*|[A-Z])\.?\s+)([A-Z][A-Za-z][A-Za-z0-9:,&()\-\/ ]{0,110})\s*$"
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def clean_page_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def resolve_existing_path(value: Any) -> Path | None:
    text = clean_text(value)
    if not text:
        return None
    path = Path(text)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, path]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate
    return None


def extract_page_texts(reader: PdfReader, max_pages: int | None = None) -> list[str]:
    limit = len(reader.pages) if max_pages is None else min(max_pages, len(reader.pages))
    return [clean_page_text(reader.pages[index].extract_text() or "") for index in range(limit)]


def flatten_outline(reader: PdfReader) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(items: Any, level: int) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item, level + 1)
                continue
            title = clean_text(getattr(item, "title", str(item)))
            if not title:
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            rows.append(
                {
                    "title": title,
                    "normalized_title": normalize_label(title),
                    "page": page,
                    "level": level,
                    "source": "outline",
                }
            )

    try:
        visit(reader.outline, 0)
    except Exception:
        return []
    return rows


def detect_text_headings(page_texts: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page_number, text in enumerate(page_texts, start=1):
        for line_number, raw_line in enumerate(text.splitlines()):
            line = " ".join(raw_line.split()).strip()
            if not line or len(line) > 140:
                continue
            title = ""
            numbered = NUMBERED_HEADING_RE.match(line)
            if numbered:
                title = numbered.group(2).strip(" .")
            elif normalize_label(line) in KNOWN_HEADINGS:
                title = line.strip(" .")
            if title and len(normalize_label(title)) >= 3:
                rows.append(
                    {
                        "title": title,
                        "normalized_title": normalize_label(title),
                        "page": page_number,
                        "level": 0,
                        "source": "text",
                        "line": line_number,
                    }
                )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (row["normalized_title"], int(row["page"]))
        if key not in seen:
            deduped.append(row)
            seen.add(key)
    return deduped


def section_map(pdf_path: Path, *, max_scan_pages: int = 20) -> dict[str, Any]:
    if max_scan_pages < 1:
        raise ValueError("max_scan_pages must be >= 1")
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"not a PDF file: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    sections = flatten_outline(reader)
    source = "outline"
    if not sections:
        sections = detect_text_headings(extract_page_texts(reader, max_scan_pages))
        source = "text"
    return {
        "pdf_path": str(pdf_path),
        "document_pages": len(reader.pages),
        "section_source": source,
        "max_scan_pages": max_scan_pages,
        "section_count": len(sections),
        "sections": sections,
    }


def find_section(sections: list[dict[str, Any]], section_query: str) -> tuple[int, dict[str, Any]]:
    target = normalize_label(section_query)
    if not target:
        raise ValueError("section query must not be empty")

    for index, section in enumerate(sections):
        if section.get("normalized_title") == target:
            return index, section
    for index, section in enumerate(sections):
        current = clean_text(section.get("normalized_title"))
        if current and (target in current or current in target):
            return index, section
    raise ValueError(f"section not found: {section_query}")


def next_boundary(sections: list[dict[str, Any]], index: int, page_count: int) -> tuple[int, dict[str, Any] | None]:
    current = sections[index]
    current_page = int(current["page"])
    current_level = int(current.get("level", 0))
    source = clean_text(current.get("source"))
    for candidate in sections[index + 1 :]:
        candidate_page = int(candidate.get("page") or 0)
        if candidate_page < current_page:
            continue
        if source == "outline":
            if int(candidate.get("level", 0)) <= current_level:
                return candidate_page, candidate
        else:
            return candidate_page, candidate
    return min(page_count + 1, current_page + 4), None


def heading_line_index(lines: list[str], title: str, *, start: int = 0) -> int | None:
    target = normalize_label(title)
    if not target:
        return None
    for index in range(start, len(lines)):
        current = normalize_label(lines[index])
        if current and (target in current or current in target):
            return index
    return None


def extract_section(
    pdf_path: Path,
    section_query: str,
    *,
    max_scan_pages: int = 20,
    max_chars: int = 20000,
) -> dict[str, Any]:
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    reader = PdfReader(str(pdf_path))
    section_payload = section_map(pdf_path, max_scan_pages=max_scan_pages)
    sections = section_payload["sections"]
    if not sections:
        raise ValueError("no sections found")

    index, matched = find_section(sections, section_query)
    start_page = int(matched["page"])
    end_exclusive, boundary = next_boundary(sections, index, len(reader.pages))
    end_exclusive = max(start_page + 1, end_exclusive)
    end_page = min(end_exclusive - 1, len(reader.pages))
    page_texts = extract_page_texts(reader, end_page)
    lines = "\n".join(page_texts[start_page - 1 : end_page]).splitlines()

    start_line = heading_line_index(lines, clean_text(matched.get("title"))) or 0
    end_line = len(lines)
    if boundary is not None:
        boundary_line = heading_line_index(lines, clean_text(boundary.get("title")), start=start_line + 1)
        if boundary_line is not None:
            end_line = boundary_line

    full_text = "\n".join(lines[start_line:end_line]).strip()
    text = full_text[:max_chars]
    return {
        "pdf_path": str(pdf_path),
        "document_pages": len(reader.pages),
        "section_query": section_query,
        "matched_section": matched,
        "next_boundary": boundary,
        "read_scope": f"section:{matched['title']}; pages:{start_page}-{end_page}",
        "text": text,
        "char_count": len(full_text),
        "truncated": len(full_text) > max_chars,
    }


def load_source_packet(survey_root: Path, paper_id: str) -> dict[str, Any]:
    path = survey_root / "synthesis" / "packets" / "index.json"
    if not path.exists():
        return {}
    return load_packet_by_id(path, paper_id) or {}


def load_candidates(survey_root: Path) -> dict[str, dict[str, Any]]:
    path = survey_root / "search" / "candidate_metadata.json"
    if not path.exists():
        return {}
    payload = load_json(path)
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return {}
    return {clean_text(candidate.get("id")): candidate for candidate in candidates if isinstance(candidate, dict)}


def resolve_survey_pdf(survey_root: Path, paper_id: str) -> tuple[Path, dict[str, Any]]:
    candidates = load_candidates(survey_root)
    packet = load_source_packet(survey_root, paper_id)
    candidate = candidates.get(paper_id, {})
    pdf_path = resolve_existing_path(packet.get("pdf_path")) or resolve_existing_path(candidate.get("local_pdf_path")) or resolve_existing_path(candidate.get("pdf_path"))
    if pdf_path is None:
        raise FileNotFoundError(f"no local PDF path found for paper id: {paper_id}")
    title = packet.get("title") or candidate.get("title")
    return pdf_path, {"id": paper_id, "title": title}


def render_section_map(payload: dict[str, Any]) -> str:
    lines = [
        f"PDF: {payload['pdf_path']}",
        f"Pages: {payload['document_pages']}",
        f"Section source: {payload['section_source']}",
        "",
    ]
    for section in payload["sections"]:
        indent = "  " * int(section.get("level", 0))
        lines.append(f"{indent}- p{section['page']}: {section['title']}")
    return "\n".join(lines).rstrip() + "\n"


def render_section(payload: dict[str, Any]) -> str:
    matched = payload["matched_section"]
    return (
        f"PDF: {payload['pdf_path']}\n"
        f"Section: {matched['title']}\n"
        f"Read scope: {payload['read_scope']}\n"
        f"Characters: {payload['char_count']}\n\n"
        f"{payload['text'].rstrip()}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", nargs="?", type=Path, help="PDF path for direct use.")
    parser.add_argument("--pdf", dest="pdf_option", type=Path, help="PDF path for direct use.")
    add_survey_root_args(parser, survey_root_help="Survey root containing reading packets and candidate metadata.")
    parser.add_argument("--paper-id", help="Candidate/paper id to resolve inside the survey workspace.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-sections", action="store_true", help="List detected sections.")
    mode.add_argument("--section", help="Extract one exact section title.")
    parser.add_argument("--max-scan-pages", type=int, default=20, help="Text fallback scan limit.")
    parser.add_argument("--max-chars", type=int, default=20000, help="Maximum extracted section characters.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--output", type=Path, help="Write JSON output to this path.")
    return parser


def resolve_input_pdf(args: argparse.Namespace) -> tuple[Path, Path | None, dict[str, Any]]:
    direct_pdf = args.pdf_option or args.pdf
    if args.pdf_option is not None and args.pdf is not None:
        raise ValueError("provide either positional PDF or --pdf, not both")
    if direct_pdf is not None and (args.topic_name or args.survey_root or args.paper_id):
        raise ValueError("direct PDF mode cannot be combined with survey mode")
    if direct_pdf is not None:
        return direct_pdf, None, {}
    if not args.paper_id:
        raise ValueError("provide a PDF path or --paper-id with --topic-name/--survey-root")
    survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
    assert survey_root is not None
    pdf_path, metadata = resolve_survey_pdf(survey_root, args.paper_id)
    return pdf_path, survey_root, metadata


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pdf_path, survey_root, metadata = resolve_input_pdf(args)
        if args.list_sections:
            payload = section_map(pdf_path, max_scan_pages=args.max_scan_pages)
        else:
            payload = extract_section(
                pdf_path,
                args.section,
                max_scan_pages=args.max_scan_pages,
                max_chars=args.max_chars,
            )
            if survey_root is not None and metadata:
                payload.update({"id": metadata["id"], "title": metadata.get("title")})
        if args.output:
            write_json(args.output, payload)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json or args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.list_sections:
        print(render_section_map(payload), end="")
    else:
        print(render_section(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
