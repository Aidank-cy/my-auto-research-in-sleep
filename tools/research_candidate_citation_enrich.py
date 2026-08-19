#!/usr/bin/env python3
"""Fill missing citationCount values in research-lit candidate metadata.

The tool reads search/candidate_metadata.json, reuses any citationCount already
present from the search layer, skips candidates with enough Hugging Face
upvotes, and only then queries Semantic Scholar for missing citation counts.

Examples
--------
python3 tools/research_candidate_citation_enrich.py \
  --topic-name agent-memory

python3 tools/research_candidate_citation_enrich.py \
  --metadata /tmp/candidate_metadata.json \
  --hf-upvote-threshold 50 \
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

from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402
import semantic_scholar_fetch  # noqa: E402

S2_FIELDS = "paperId,title,year,venue,externalIds,citationCount,url"
DEFAULT_HF_UPVOTE_THRESHOLD = 50
SEMANTIC_SCHOLAR_CITATION_RETRIES = 15
SEMANTIC_SCHOLAR_CITATION_RETRY_DELAY_SECONDS = 2.0
TRANSIENT_S2_ERRORS = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "Network error")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def citation_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def hf_upvotes(candidate: dict[str, Any]) -> int:
    hf = candidate.get("hf")
    if not isinstance(hf, dict):
        return 0
    try:
        return int(hf.get("upvotes") or 0)
    except (TypeError, ValueError):
        return 0


ALLOWED_RELEVANCE = {0.0, 0.5, 1.0}


def relevance_value(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number in ALLOWED_RELEVANCE else None


def validate_admitted_relevance(metadata: dict[str, Any], admitted: Any) -> list[str]:
    if not isinstance(admitted, list):
        return ["candidate_papers.json must contain a list"]
    candidates = metadata.get("candidates") if isinstance(metadata, dict) else None
    if not isinstance(candidates, list):
        return ["candidate metadata must contain a candidates list"]
    metadata_by_id: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("id"):
            metadata_by_id.setdefault(str(candidate["id"]), []).append(candidate)
    errors: list[str] = []
    seen: set[str] = set()
    for record in admitted:
        candidate_id = str(record.get("id")) if isinstance(record, dict) and record.get("id") else ""
        if not candidate_id:
            errors.append("candidate_papers.json contains a record without id")
            continue
        if candidate_id in seen:
            errors.append(f"duplicate admitted candidate id: {candidate_id}")
            continue
        seen.add(candidate_id)
        matches = metadata_by_id.get(candidate_id, [])
        if len(matches) != 1:
            errors.append(f"admitted candidate {candidate_id} must map to exactly one metadata record")
            continue
        if relevance_value(matches[0].get("relevance")) is None:
            errors.append(f"admitted candidate {candidate_id} must have numeric relevance 0, 0.5, or 1")
    return errors


def is_transient_s2_error(error: str | None) -> bool:
    if not error:
        return False
    return any(marker in error for marker in TRANSIENT_S2_ERRORS)


def existing_citation(candidate: dict[str, Any]) -> tuple[int | None, str | None]:
    top_level = citation_value(candidate.get("citationCount"))
    if top_level is not None:
        return top_level, "candidate_metadata"

    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict):
        nested = citation_value(semantic_scholar.get("citationCount"))
        if nested is not None:
            return nested, "semantic_scholar"

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        s2_payload = source_payloads.get("semantic-scholar")
        if isinstance(s2_payload, dict):
            payload_count = citation_value(s2_payload.get("citationCount"))
            if payload_count is not None:
                return payload_count, "source_payloads.semantic-scholar"

    return None, None


def candidate_identifiers(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    identifiers: list[tuple[str, str]] = []
    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict) and semantic_scholar.get("paperId"):
        identifiers.append(("semantic_scholar_paper_id", str(semantic_scholar["paperId"])))

    arxiv_id = candidate.get("arxiv_id")
    if arxiv_id:
        identifiers.append(("arxiv_id", f"ARXIV:{arxiv_id}"))

    doi = candidate.get("doi")
    if doi:
        identifiers.append(("doi", str(doi)))

    title = candidate.get("title")
    if title:
        identifiers.append(("title", str(title)))

    return identifiers


def fetch_semantic_scholar_with_retry(
    kind: str,
    value: str,
    *,
    retries: int = SEMANTIC_SCHOLAR_CITATION_RETRIES,
    retry_delay: float = SEMANTIC_SCHOLAR_CITATION_RETRY_DELAY_SECONDS,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one Semantic Scholar record using the citation-enrichment policy.

    This is the single policy wrapper for title searches and identifier lookups.
    Change its defaults or pass explicit values to tune citation enrichment
    without changing the candidate loop or the low-level S2 client.
    """
    try:
        if kind == "title":
            result = semantic_scholar_fetch.search(
                value,
                max_results=1,
                fields=S2_FIELDS,
                retries=retries,
                retry_delay=retry_delay,
            )
            papers = result.get("data") or []
            if not papers:
                return None, "title_search_no_results"
            paper = papers[0]
            if normalize_title(paper.get("title")) != normalize_title(value):
                return None, "title_search_no_exact_match"
            return paper, None

        return semantic_scholar_fetch.get_paper(
            value,
            fields=S2_FIELDS,
            retries=retries,
            retry_delay=retry_delay,
        ), None
    except Exception as exc:
        return None, str(exc)


def fetch_by_identifier(kind: str, value: str) -> tuple[dict[str, Any] | None, str | None]:
    """Backward-compatible alias for the centralized S2 citation wrapper."""
    return fetch_semantic_scholar_with_retry(kind, value)


def fill_candidate(
    candidate: dict[str, Any],
    *,
    hf_threshold: int,
    dry_run: bool,
    positive_relevance_only: bool,
) -> tuple[dict[str, Any], bool]:
    record = {
        "id": candidate.get("id"),
        "title": candidate.get("title"),
        "action": None,
        "citationCount": None,
        "source": None,
        "identifier": None,
        "reason": None,
        "hf_upvotes": hf_upvotes(candidate),
    }

    if positive_relevance_only and (relevance_value(candidate.get("relevance")) or 0) <= 0:
        record.update(action="skipped_relevance", reason="relevance <= 0 or candidate not admitted")
        return record, False

    count, source = existing_citation(candidate)
    if count is not None:
        record.update(action="reused_existing", citationCount=count, source=source)
        did_change = not dry_run and candidate.get("citationCount") != count
        if not dry_run:
            candidate["citationCount"] = count
        return record, did_change

    if record["hf_upvotes"] >= hf_threshold:
        record.update(
            action="skipped_hf_upvotes",
            reason=f"hf_upvotes >= {hf_threshold}",
        )
        return record, False

    identifiers = candidate_identifiers(candidate)
    if not identifiers:
        record.update(action="skipped_no_identifier", reason="no arxiv_id, doi, Semantic Scholar paperId, or title")
        return record, False

    if dry_run:
        kind, value = identifiers[0]
        record.update(action="would_fetch", identifier={"kind": kind, "value": value})
        return record, False

    old_key = os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    try:
        for kind, value in identifiers:
            paper, error = fetch_by_identifier(kind, value)
            if paper is None:
                record.update(action="fetch_failed", identifier={"kind": kind, "value": value}, reason=error)
                if is_transient_s2_error(error):
                    break
                continue

            count = citation_value(paper.get("citationCount"))
            if count is None:
                record.update(action="fetch_failed", identifier={"kind": kind, "value": value}, reason="missing citationCount")
                continue

            candidate["citationCount"] = count
            semantic_scholar = candidate.setdefault("semantic_scholar", {})
            if isinstance(semantic_scholar, dict):
                if paper.get("paperId"):
                    semantic_scholar.setdefault("paperId", paper.get("paperId"))
                if paper.get("externalIds"):
                    semantic_scholar.setdefault("externalIds", paper.get("externalIds"))
            record.update(
                action="enriched",
                citationCount=count,
                source="semantic-scholar",
                identifier={"kind": kind, "value": value},
            )
            return record, True
    finally:
        if old_key is not None:
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = old_key

    if record["action"] == "fetch_failed":
        return record, False
    record.update(action="not_found", reason="all identifiers failed")
    return record, False


def load_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidate metadata must be an object with a candidates list")
    return payload


def load_metadata_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("candidate_papers.json must contain a list of objects")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument("--metadata", type=Path, help="Explicit candidate_metadata.json path.")
    parser.add_argument("--report", type=Path, help="Output report path; default search/citation_enrichment.json.")
    parser.add_argument("--hf-upvote-threshold", type=int, default=DEFAULT_HF_UPVOTE_THRESHOLD)
    parser.add_argument("--limit", type=int, help="Process at most N candidates, useful for debugging.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between Semantic Scholar fetches.")
    parser.add_argument(
        "--positive-relevance-only",
        action="store_true",
        help="Only enrich candidates with relevance 0.5 or 1.",
    )
    parser.add_argument("--candidate-papers", type=Path, help="Admitted-candidate list; default next to metadata.")
    parser.add_argument("--dry-run", action="store_true", help="Plan actions without writing metadata or calling Semantic Scholar.")
    return parser


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    survey_root = resolve_survey_root(args.survey_root, args.topic_name, required=False)
    if args.metadata:
        metadata_path = args.metadata
    elif survey_root:
        metadata_path = survey_root / "search" / "candidate_metadata.json"
    else:
        raise ValueError("provide --topic-name, --survey-root, or --metadata")

    report_path = args.report or (metadata_path.parent / "citation_enrichment.json")
    candidate_papers_path = args.candidate_papers or (metadata_path.parent / "candidate_papers.json")
    return metadata_path, report_path, candidate_papers_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.hf_upvote_threshold < 0:
        print("Error: --hf-upvote-threshold must be >= 0", file=sys.stderr)
        return 2

    try:
        metadata_path, report_path, candidate_papers_path = resolve_paths(args)
        metadata = load_metadata(metadata_path)
        admitted = load_metadata_list(candidate_papers_path)
        relevance_errors = validate_admitted_relevance(metadata, admitted)
        if relevance_errors:
            raise ValueError("; ".join(relevance_errors))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    admitted_ids = {str(record["id"]) for record in admitted}
    candidates = [candidate for candidate in metadata["candidates"] if str(candidate.get("id")) in admitted_ids]
    if args.limit is not None:
        candidates_to_process = candidates[: max(args.limit, 0)]
    else:
        candidates_to_process = candidates

    started = time.time()
    records: list[dict[str, Any]] = []
    changed = 0
    for index, candidate in enumerate(candidates_to_process):
        record, did_change = fill_candidate(
            candidate,
            hf_threshold=args.hf_upvote_threshold,
            dry_run=args.dry_run,
            positive_relevance_only=args.positive_relevance_only,
        )
        records.append(record)
        changed += int(did_change)
        if index < len(candidates_to_process) - 1 and record["action"] in {"enriched", "fetch_failed", "not_found"}:
            time.sleep(args.sleep)

    counts: dict[str, int] = {}
    for record in records:
        action = str(record.get("action"))
        counts[action] = counts.get(action, 0) + 1

    report = {
        "metadata_path": str(metadata_path),
        "hf_upvote_threshold": args.hf_upvote_threshold,
        "positive_relevance_only": args.positive_relevance_only,
        "dry_run": args.dry_run,
        "processed": len(records),
        "changed": changed,
        "counts": counts,
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
