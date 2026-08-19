#!/usr/bin/env python3
"""Build one full-text source packet for every selected survey paper."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import read_pdf_front_pages  # noqa: E402
import research_note_tasks  # noqa: E402
from research_artifact_io import sha256_file, sha256_text  # noqa: E402
from research_packet_io import write_packet_bundle  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402


VALID_EVIDENCE_LEVELS = {"fulltext", "local-note", "metadata-only"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def unique_records(payload: Any, *, list_key: str, label: str) -> list[dict[str, Any]]:
    records = payload.get(list_key) if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{label} must be an object with a {list_key} list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"{label} record at index {index} is not an object")
        record_id = clean_text(record.get("id"))
        if not record_id:
            raise ValueError(f"{label} record at index {index} is missing id")
        if record_id in seen:
            raise ValueError(f"duplicate {label} id: {record_id}")
        seen.add(record_id)
        result.append(record)
    return result


def metadata_text(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for label, key in (("Title", "title"), ("Abstract", "abstract"), ("TLDR", "tldr")):
        value = clean_text(candidate.get(key))
        if value:
            parts.append(f"{label}: {value}")
    if not parts:
        fallback = clean_text(candidate.get("relevanceText"))
        if fallback:
            parts.append(fallback)
    return "\n\n".join(parts)


def resolve_existing_path(value: Any, *, suffixes: set[str]) -> Path | None:
    text = clean_text(value)
    if not text:
        return None
    path = Path(text)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, path]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in suffixes:
            return candidate.resolve()
    return None


def resolve_pdf(candidate: dict[str, Any], downloaded_dir: Path) -> Path | None:
    for key in ("local_pdf_path", "pdf_path"):
        path = resolve_existing_path(candidate.get(key), suffixes={".pdf"})
        if path is not None:
            return path
    arxiv_id = clean_text(candidate.get("arxiv_id"))
    if arxiv_id:
        cached = downloaded_dir / f"{arxiv_id.replace('/', '_')}.pdf"
        if cached.exists() and cached.is_file():
            return cached.resolve()
    return None


def resolve_paper_note(
    candidate: dict[str, Any],
    *,
    trusted_note_paths: set[Path],
    trusted_note_roots: list[Path],
) -> Path | None:
    path = resolve_existing_path(candidate.get("local_note_path"), suffixes={".md", ".txt"})
    if path is None:
        return None
    if path in trusted_note_paths or any(path.is_relative_to(root) for root in trusted_note_roots):
        return path
    return None


def page_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for page in payload.get("pages", []):
        text = clean_text(page.get("text"))
        if text:
            chunks.append(f"--- Page {page.get('page')} ---\n{text}")
    return "\n\n".join(chunks)


def base_packet(record: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": clean_text(record.get("id")),
        "rank": record.get("rank"),
        "title": clean_text(candidate.get("title")) or clean_text(record.get("title")),
        "status": "ok",
        "source_kind": None,
        "source_path": None,
        "source_warning": None,
        "evidence_level": None,
        "pdf_path": None,
        "read_scope": None,
        "document_pages": None,
        "pages_read": None,
        "source_sha256": None,
        "text_sha256": None,
        "text": None,
    }


def build_packet(
    record: dict[str, Any],
    candidate: dict[str, Any],
    *,
    downloaded_dir: Path,
    trusted_note_paths: set[Path],
    trusted_note_roots: list[Path],
) -> dict[str, Any]:
    packet = base_packet(record, candidate)
    pdf_path = resolve_pdf(candidate, downloaded_dir)
    if pdf_path is not None:
        packet.update(source_kind="pdf", source_path=str(pdf_path), pdf_path=str(pdf_path))
        try:
            extracted = read_pdf_front_pages.extract_fulltext(pdf_path)
            text = page_text(extracted)
        except Exception as exc:
            packet.update(status="error", source_warning=f"fulltext_read_failed: {exc}")
            return packet
        document_pages = int(extracted.get("document_pages") or 0)
        pages_read = int(extracted.get("pages_read") or 0)
        if not text or document_pages < 1 or pages_read != document_pages:
            packet.update(status="error", source_warning="incomplete_or_empty_fulltext")
            return packet
        packet.update(
            evidence_level="fulltext",
            read_scope=f"pages:1-{document_pages}",
            document_pages=document_pages,
            pages_read=pages_read,
            source_sha256=sha256_file(pdf_path),
            text_sha256=sha256_text(text),
            text=text,
        )
        return packet

    note_path = resolve_paper_note(
        candidate,
        trusted_note_paths=trusted_note_paths,
        trusted_note_roots=trusted_note_roots,
    )
    if note_path is not None:
        text = note_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            packet.update(
                source_kind="local-note",
                source_path=str(note_path),
                source_warning="secondary_source",
                evidence_level="local-note",
                read_scope=f"note:{note_path}",
                source_sha256=sha256_file(note_path),
                text_sha256=sha256_text(text),
                text=text,
            )
            return packet

    text = metadata_text(candidate)
    if not text:
        packet.update(status="error", source_kind="metadata", source_warning="empty_metadata_text")
        return packet
    packet.update(
        source_kind="metadata",
        source_warning="pdf_unavailable",
        evidence_level="metadata-only",
        read_scope="metadata",
        text_sha256=sha256_text(text),
        text=text,
    )
    return packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing search metadata and ranking.")
    parser.add_argument("--output-dir", type=Path, help="Default: <survey-root>/synthesis/packets")
    parser.add_argument(
        "--trusted-note-root",
        type=Path,
        action="append",
        default=[],
        help="Additional root whose local Markdown notes may be used as secondary evidence.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
        wiki_notes = research_note_tasks.load_validated_wiki_notes(survey_root)
        metadata_path = survey_root / "search" / "candidate_metadata.json"
        ranking_path = survey_root / "search" / "candidate_ranking.json"
        metadata = load_json(metadata_path)
        ranking = load_json(ranking_path)
        candidates = unique_records(metadata, list_key="candidates", label="candidate metadata")
        ranked = unique_records(ranking, list_key="ranked_candidates", label="candidate ranking")
        selected = [record for record in ranked if record.get("selected") is True]
        if not selected:
            raise ValueError("candidate ranking contains no selected papers")
        candidates_by_id = {clean_text(record["id"]): record for record in candidates}
        missing_ids = [clean_text(record["id"]) for record in selected if clean_text(record["id"]) not in candidates_by_id]
        if missing_ids:
            raise ValueError(f"selected paper IDs missing from candidate metadata: {missing_ids}")
        trusted_note_paths = {
            Path(clean_text(record.get("note_path"))).resolve()
            for record in wiki_notes if isinstance(record, dict) and clean_text(record.get("note_path"))
        }
        missing_routes = [str(path) for path in trusted_note_paths if not path.is_file()]
        if missing_routes:
            raise ValueError(f"trusted wiki note is missing: {missing_routes[0]}")
        default_root = (REPO_ROOT / "database" / "wiki" / "Papers & Blogs").resolve()
        trusted_note_roots = [default_root, *(path.resolve() for path in args.trusted_note_root)]
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    downloaded_dir = survey_root / "papers" / "downloaded"
    packets = [
        build_packet(
            record,
            candidates_by_id[clean_text(record["id"])],
            downloaded_dir=downloaded_dir,
            trusted_note_paths=trusted_note_paths,
            trusted_note_roots=trusted_note_roots,
        )
        for record in selected
    ]
    output_dir = args.output_dir or (survey_root / "synthesis" / "packets")
    index_path = write_packet_bundle(
        output_dir,
        index_metadata={
        "survey_root": str(survey_root),
        "candidate_metadata": str(metadata_path),
        "candidate_ranking": str(ranking_path),
        },
        packets=packets,
    )
    errors = [packet for packet in packets if packet["status"] != "ok"]
    print(
        json.dumps(
            {
                "output": str(index_path),
                "packet_count": len(packets),
                "fulltext": sum(1 for packet in packets if packet["evidence_level"] == "fulltext"),
                "local_note": sum(1 for packet in packets if packet["evidence_level"] == "local-note"),
                "metadata_only": sum(1 for packet in packets if packet["evidence_level"] == "metadata-only"),
                "errors": [{"id": packet["id"], "reason": packet["source_warning"]} for packet in errors],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
