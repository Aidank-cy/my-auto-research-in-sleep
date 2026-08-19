#!/usr/bin/env python3
"""Fill missing basic metadata in research-lit candidate metadata.

This tool reads search/candidate_metadata.json, fills empty metadata fields from
free public arXiv and, when enabled, unauthenticated Semantic Scholar calls, and
writes the metadata file back in place. It does not request citationCount and
does not compute any ranking or importance score.

Examples
--------
python3 tools/research_candidate_metadata_hydrate.py \
  --topic-name agent-memory

python3 tools/research_candidate_metadata_hydrate.py \
  --metadata /tmp/candidate_metadata.json \
  --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_fetch  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402
import semantic_scholar_fetch  # noqa: E402

S2_METADATA_FIELDS = (
    "paperId,title,abstract,year,venue,publicationVenue,publicationTypes,"
    "publicationDate,url,openAccessPdf,authors,externalIds,fieldsOfStudy,"
    "s2FieldsOfStudy,tldr"
)
ARXIV_FIELDS = ("title", "authors", "abstract", "published", "updated", "year", "categories", "abs_url", "pdf_url")
S2_TOP_FIELDS = ("title", "authors", "abstract", "year", "venue", "doi", "publicationTypes", "publicationDate", "tldr", "url", "pdf_url")
MAX_RELEVANCE_TEXT_CHARS = 3000


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    return text or None


def is_missing(value: Any) -> bool:
    return value in (None, "", [], {})


def normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def truncate_text(value: str, max_chars: int = MAX_RELEVANCE_TEXT_CHARS) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def relevance_text(candidate: dict[str, Any]) -> str | None:
    title = clean_text(candidate.get("title"))
    prefix = f"Title: {title}\n" if title else ""

    abstract = clean_text(candidate.get("abstract"))
    if abstract:
        return truncate_text(f"{prefix}Abstract: {abstract}")

    tldr = clean_text(candidate.get("tldr"))
    if tldr:
        return truncate_text(f"{prefix}TLDR: {tldr}")

    hf = candidate.get("hf")
    if isinstance(hf, dict):
        ai_summary = clean_text(hf.get("ai_summary"))
        if ai_summary:
            return truncate_text(f"{prefix}Hugging Face AI summary: {ai_summary}")
        summary = clean_text(hf.get("summary"))
        if summary:
            return truncate_text(f"{prefix}Hugging Face summary: {summary}")

    if title:
        return truncate_text(f"Title: {title}")
    return None


def set_if_missing(candidate: dict[str, Any], key: str, value: Any) -> bool:
    if is_missing(value) or not is_missing(candidate.get(key)):
        return False
    candidate[key] = value
    return True


def set_nested_if_missing(candidate: dict[str, Any], group: str, key: str, value: Any) -> bool:
    if is_missing(value):
        return False
    current = candidate.get(group)
    if not isinstance(current, dict):
        current = {}
        candidate[group] = current
    if not is_missing(current.get(key)):
        return False
    current[key] = value
    return True


def s2_author_names(authors: Any) -> list[str] | None:
    if not isinstance(authors, list):
        return None
    names = []
    for author in authors:
        if isinstance(author, dict):
            name = clean_text(author.get("name"))
            if name:
                names.append(name)
    return names or None


def s2_tldr_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("text"))
    return clean_text(value)


def open_pdf_url(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("url"))
    return None


def needs_arxiv(candidate: dict[str, Any]) -> bool:
    return bool(candidate.get("arxiv_id")) and any(is_missing(candidate.get(key)) for key in ARXIV_FIELDS)


def s2_identifiers(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    identifiers: list[tuple[str, str]] = []
    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict) and semantic_scholar.get("paperId"):
        identifiers.append(("semantic_scholar_paper_id", str(semantic_scholar["paperId"])))

    arxiv_id = clean_text(candidate.get("arxiv_id"))
    if arxiv_id:
        identifiers.append(("arxiv_id", f"ARXIV:{arxiv_id}"))

    doi = clean_text(candidate.get("doi"))
    if doi:
        identifiers.append(("doi", doi))

    title = clean_text(candidate.get("title"))
    if title:
        identifiers.append(("title", title))

    return identifiers


def needs_s2(candidate: dict[str, Any]) -> bool:
    if not s2_identifiers(candidate):
        return False
    if any(is_missing(candidate.get(key)) for key in S2_TOP_FIELDS):
        return True
    semantic_scholar = candidate.get("semantic_scholar")
    if not isinstance(semantic_scholar, dict):
        return True
    return is_missing(semantic_scholar.get("paperId")) or is_missing(semantic_scholar.get("externalIds"))


def hydrate_from_arxiv(candidate: dict[str, Any], paper: dict[str, Any]) -> list[str]:
    filled: list[str] = []
    published = clean_text(paper.get("published"))
    values = {
        "title": clean_text(paper.get("title")),
        "authors": paper.get("authors"),
        "abstract": clean_text(paper.get("abstract")),
        "published": published,
        "updated": clean_text(paper.get("updated")),
        "year": published[:4] if published else None,
        "categories": paper.get("categories"),
        "abs_url": clean_text(paper.get("abs_url")),
        "pdf_url": clean_text(paper.get("pdf_url")),
    }
    for key, value in values.items():
        if set_if_missing(candidate, key, value):
            filled.append(key)
    return filled


def hydrate_from_s2(candidate: dict[str, Any], paper: dict[str, Any]) -> list[str]:
    filled: list[str] = []
    external = paper.get("externalIds") or {}
    doi = external.get("DOI") or external.get("doi")
    values = {
        "title": clean_text(paper.get("title")),
        "authors": s2_author_names(paper.get("authors")),
        "abstract": clean_text(paper.get("abstract")),
        "year": paper.get("year"),
        "venue": clean_text(paper.get("venue")),
        "doi": clean_text(doi),
        "publicationTypes": paper.get("publicationTypes"),
        "publicationDate": clean_text(paper.get("publicationDate")),
        "tldr": s2_tldr_text(paper.get("tldr")),
        "url": clean_text(paper.get("url")),
        "pdf_url": open_pdf_url(paper.get("openAccessPdf")),
    }
    for key, value in values.items():
        if set_if_missing(candidate, key, value):
            filled.append(key)

    nested_values = {
        "paperId": paper.get("paperId"),
        "externalIds": external,
        "fieldsOfStudy": paper.get("fieldsOfStudy"),
        "s2FieldsOfStudy": paper.get("s2FieldsOfStudy"),
        "publicationVenue": paper.get("publicationVenue"),
    }
    for key, value in nested_values.items():
        if set_nested_if_missing(candidate, "semantic_scholar", key, value):
            filled.append(f"semantic_scholar.{key}")
    return filled


def refresh_relevance_text(candidate: dict[str, Any]) -> bool:
    text = relevance_text(candidate)
    if not text or candidate.get("relevanceText") == text:
        return False
    candidate["relevanceText"] = text
    return True


def fetch_arxiv(candidate: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    arxiv_id = clean_text(candidate.get("arxiv_id"))
    if not arxiv_id:
        return None, "missing arxiv_id"
    try:
        papers = arxiv_fetch.search(f"id:{arxiv_id}", max_results=1)
    except Exception as exc:
        return None, str(exc)
    if not papers:
        return None, "arxiv_not_found"
    return papers[0], None


def fetch_s2_by_identifier(kind: str, value: str) -> tuple[dict[str, Any] | None, str | None]:
    old_key = os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    try:
        if kind == "title":
            result = semantic_scholar_fetch.search(value, max_results=1, fields=S2_METADATA_FIELDS)
            papers = result.get("data") or []
            if not papers:
                return None, "title_search_no_results"
            paper = papers[0]
            if normalize_title(paper.get("title")) != normalize_title(value):
                return None, "title_search_no_exact_match"
            return paper, None
        return semantic_scholar_fetch.get_paper(value, fields=S2_METADATA_FIELDS), None
    except Exception as exc:
        return None, str(exc)
    finally:
        if old_key is not None:
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = old_key


def hydrate_candidate(
    candidate: dict[str, Any],
    *,
    dry_run: bool,
    skip_arxiv: bool,
    skip_s2: bool,
) -> tuple[dict[str, Any], bool, bool]:
    record = {
        "id": candidate.get("id"),
        "title": candidate.get("title"),
        "arxiv": {"action": None, "filled": [], "reason": None},
        "semantic_scholar": {"action": None, "filled": [], "identifier": None, "reason": None},
    }
    changed = False
    called_network = False

    if skip_arxiv:
        record["arxiv"]["action"] = "skipped_disabled"
    elif not needs_arxiv(candidate):
        record["arxiv"]["action"] = "skipped_not_needed"
    elif dry_run:
        record["arxiv"]["action"] = "would_fetch"
    else:
        called_network = True
        paper, error = fetch_arxiv(candidate)
        if paper is None:
            record["arxiv"].update(action="fetch_failed", reason=error)
        else:
            filled = hydrate_from_arxiv(candidate, paper)
            record["arxiv"].update(action="hydrated" if filled else "fetched_no_change", filled=filled)
            changed = changed or bool(filled)

    if skip_s2:
        record["semantic_scholar"]["action"] = "skipped_disabled"
    elif not needs_s2(candidate):
        record["semantic_scholar"]["action"] = "skipped_not_needed"
    elif dry_run:
        identifiers = s2_identifiers(candidate)
        record["semantic_scholar"].update(
            action="would_fetch",
            identifier={"kind": identifiers[0][0], "value": identifiers[0][1]} if identifiers else None,
        )
    else:
        for kind, value in s2_identifiers(candidate):
            called_network = True
            paper, error = fetch_s2_by_identifier(kind, value)
            record["semantic_scholar"]["identifier"] = {"kind": kind, "value": value}
            if paper is None:
                record["semantic_scholar"].update(action="fetch_failed", reason=error)
                continue
            filled = hydrate_from_s2(candidate, paper)
            record["semantic_scholar"].update(
                action="hydrated" if filled else "fetched_no_change",
                filled=filled,
                reason=None,
            )
            changed = changed or bool(filled)
            break

    if not dry_run and refresh_relevance_text(candidate):
        record.setdefault("derived", {})["relevanceText"] = "refreshed"
        changed = True

    return record, changed, called_network


def load_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidate metadata must be an object with a candidates list")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument("--metadata", type=Path, help="Explicit candidate_metadata.json path.")
    parser.add_argument("--report", type=Path, help="Output report path; default search/metadata_hydration.json.")
    parser.add_argument("--limit", type=int, help="Process at most N candidates, useful for debugging.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay after candidates that triggered network calls.")
    parser.add_argument("--dry-run", action="store_true", help="Plan actions without writing metadata or calling network APIs.")
    parser.add_argument("--skip-arxiv", action="store_true")
    parser.add_argument("--skip-s2", action="store_true")
    return parser


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    survey_root = resolve_survey_root(args.survey_root, args.topic_name, required=False)
    if args.metadata:
        metadata_path = args.metadata
    elif survey_root:
        metadata_path = survey_root / "search" / "candidate_metadata.json"
    else:
        raise ValueError("provide --topic-name, --survey-root, or --metadata")

    report_path = args.report or (metadata_path.parent / "metadata_hydration.json")
    return metadata_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata_path, report_path = resolve_paths(args)
        metadata = load_metadata(metadata_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    candidates = metadata["candidates"]
    if args.limit is not None:
        candidates_to_process = candidates[: max(args.limit, 0)]
    else:
        candidates_to_process = candidates

    started = time.time()
    records: list[dict[str, Any]] = []
    changed = 0
    network_candidates = 0
    for index, candidate in enumerate(candidates_to_process):
        record, did_change, called_network = hydrate_candidate(
            candidate,
            dry_run=args.dry_run,
            skip_arxiv=args.skip_arxiv,
            skip_s2=args.skip_s2,
        )
        records.append(record)
        changed += int(did_change)
        network_candidates += int(called_network)
        if called_network and index < len(candidates_to_process) - 1:
            time.sleep(args.sleep)

    report = {
        "metadata_path": str(metadata_path),
        "dry_run": args.dry_run,
        "processed": len(records),
        "changed": changed,
        "network_candidates": network_candidates,
        "elapsed_seconds": round(time.time() - started, 2),
        "records": records,
    }

    if not args.dry_run:
        write_json(metadata_path, metadata)
        write_json(report_path, report)

    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
