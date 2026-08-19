#!/usr/bin/env python3
"""Rank research-lit candidates for evidence-card selection.

The tool reads ``search/candidate_metadata.json`` and writes a standalone
ranking report. It does not mutate candidate metadata.

Ranking contract:
1. Require an Agent-assigned relevance value of 1, 0.5, or 0.
2. Admit verified candidates with positive relevance and sort 1 before 0.5.
3. Within each relevance bucket, put citation-qualified papers first.
4. Then put HF-upvote-qualified papers that were not citation-qualified.
5. Keep all remaining papers in original candidate order.

Examples
--------
python3 tools/research_candidate_rank.py \
  --topic-name agent-memory

python3 tools/research_candidate_rank.py \
  --metadata /tmp/candidate_metadata.json \
  --max-selected 20 \
  --citation-threshold 50 \
  --upvote-threshold 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402

ALLOWED_RELEVANCE = (1.0, 0.5, 0.0)
DEFAULT_CITATION_THRESHOLD = 50
DEFAULT_UPVOTE_THRESHOLD = 50
DEFAULT_MAX_SELECTED = 20


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def citation_count(candidate: dict[str, Any]) -> int:
    top_level = int_value(candidate.get("citationCount"))
    if top_level is not None:
        return top_level

    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict):
        nested = int_value(semantic_scholar.get("citationCount"))
        if nested is not None:
            return nested

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        s2_payload = source_payloads.get("semantic-scholar")
        if isinstance(s2_payload, dict):
            payload_count = int_value(s2_payload.get("citationCount"))
            if payload_count is not None:
                return payload_count

    return 0


def hf_upvotes(candidate: dict[str, Any]) -> int:
    hf = candidate.get("hf")
    if isinstance(hf, dict):
        votes = int_value(hf.get("upvotes"))
        if votes is not None:
            return votes

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        hf_payload = source_payloads.get("huggingface-papers")
        if isinstance(hf_payload, dict):
            votes = int_value(hf_payload.get("upvotes"))
            if votes is not None:
                return votes

    return 0


def relevance_value(candidate: dict[str, Any], *, required: bool = True) -> float:
    raw = candidate.get("relevance")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        if not required:
            return 0.0
        raise ValueError(f"candidate {candidate.get('id')} must have numeric relevance 0, 0.5, or 1")
    value = float(raw)
    if value not in ALLOWED_RELEVANCE:
        if not required:
            return 0.0
        raise ValueError(f"candidate {candidate.get('id')} has invalid relevance={raw!r}; expected 0, 0.5, or 1")
    return value


def importance_bucket(
    candidate: dict[str, Any],
    *,
    citation_threshold: int,
    upvote_threshold: int,
) -> str:
    if citation_count(candidate) >= citation_threshold:
        return "citation"
    if hf_upvotes(candidate) >= upvote_threshold:
        return "upvotes"
    return "none"


def sort_key(record: dict[str, Any]) -> tuple[float, int, int, int]:
    bucket_order = {"citation": 0, "upvotes": 1, "none": 2}
    bucket = record["importance_bucket"]
    if bucket == "citation":
        magnitude = -record["citationCount"]
    elif bucket == "upvotes":
        magnitude = -record["hf_upvotes"]
    else:
        magnitude = 0
    return (-record["relevance"], bucket_order[bucket], magnitude, record["original_index"])


def minimal_record(
    candidate: dict[str, Any],
    *,
    original_index: int,
    citation_threshold: int,
    upvote_threshold: int,
) -> dict[str, Any]:
    verification_status = candidate.get("verification_status")
    relevance = relevance_value(candidate, required=verification_status == "verified")
    citations = citation_count(candidate)
    upvotes = hf_upvotes(candidate)
    bucket = importance_bucket(
        candidate,
        citation_threshold=citation_threshold,
        upvote_threshold=upvote_threshold,
    )
    eligible = verification_status == "verified" and relevance > 0
    if verification_status != "verified":
        exclusion_reason = "not_verified"
    elif relevance == 0:
        exclusion_reason = "not_relevant"
    else:
        exclusion_reason = None
    return {
        "rank": None,
        "selected": False,
        "eligible": eligible,
        "exclusion_reason": exclusion_reason,
        "id": candidate.get("id"),
        "title": candidate.get("title"),
        "relevance": relevance,
        "importance_bucket": bucket,
        "citationCount": citations,
        "hf_upvotes": upvotes,
        "original_index": original_index,
        "sources": candidate.get("sources", []),
        "arxiv_id": candidate.get("arxiv_id"),
        "doi": candidate.get("doi"),
        "verification_status": verification_status,
        "verification_method": candidate.get("verification_method"),
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    citation_threshold: int = DEFAULT_CITATION_THRESHOLD,
    upvote_threshold: int = DEFAULT_UPVOTE_THRESHOLD,
    max_selected: int = DEFAULT_MAX_SELECTED,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        record = minimal_record(
            candidate,
            original_index=index,
            citation_threshold=citation_threshold,
            upvote_threshold=upvote_threshold,
        )
        records.append(record)

    records.sort(key=lambda record: (0 if record["eligible"] else 1, *sort_key(record)))
    eligible_rank = 0
    for record in records:
        if not record["eligible"]:
            continue
        eligible_rank += 1
        record["rank"] = eligible_rank
        record["selected"] = eligible_rank <= max_selected

    selected = [record for record in records if record["selected"]]
    buckets: list[dict[str, Any]] = []
    for relevance in ALLOWED_RELEVANCE:
        subset = [record for record in records if record["relevance"] == relevance]
        buckets.append(
            {
                "relevance": relevance,
                "candidate_count": len(subset),
                "citation_count": sum(1 for record in subset if record["importance_bucket"] == "citation"),
                "upvotes_count": sum(1 for record in subset if record["importance_bucket"] == "upvotes"),
                "none_count": sum(1 for record in subset if record["importance_bucket"] == "none"),
            }
        )

    return {
        "citation_threshold": citation_threshold,
        "upvote_threshold": upvote_threshold,
        "max_selected": max_selected,
        "candidate_count": len(records),
        "eligible_count": sum(record["eligible"] for record in records),
        "selected_count": len(selected),
        "selected_candidate_ids": [record["id"] for record in selected],
        "buckets": buckets,
        "warnings": [],
        "ranked_candidates": records,
    }


def load_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidate metadata must be an object with a candidates list")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument("--metadata", type=Path, help="Explicit candidate_metadata.json path.")
    parser.add_argument("--output", type=Path, help="Output ranking path; default search/candidate_ranking.json.")
    parser.add_argument("--citation-threshold", type=int, default=DEFAULT_CITATION_THRESHOLD)
    parser.add_argument("--upvote-threshold", type=int, default=DEFAULT_UPVOTE_THRESHOLD)
    parser.add_argument("--max-selected", type=int, default=DEFAULT_MAX_SELECTED)
    return parser


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    survey_root = resolve_survey_root(args.survey_root, args.topic_name, required=False)
    if args.metadata:
        metadata_path = args.metadata
    elif survey_root:
        metadata_path = survey_root / "search" / "candidate_metadata.json"
    else:
        raise ValueError("provide --topic-name, --survey-root, or --metadata")

    output_path = args.output or (metadata_path.parent / "candidate_ranking.json")
    return metadata_path, output_path, survey_root


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.citation_threshold < 0:
        print("Error: --citation-threshold must be >= 0", file=sys.stderr)
        return 2
    if args.upvote_threshold < 0:
        print("Error: --upvote-threshold must be >= 0", file=sys.stderr)
        return 2
    if args.max_selected < 0:
        print("Error: --max-selected must be >= 0", file=sys.stderr)
        return 2

    try:
        metadata_path, output_path, survey_root = resolve_paths(args)
        metadata = load_metadata(metadata_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        report = rank_candidates(
            metadata["candidates"],
            citation_threshold=args.citation_threshold,
            upvote_threshold=args.upvote_threshold,
            max_selected=args.max_selected,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    report.update(
        {
            "metadata_path": str(metadata_path),
            "output_path": str(output_path),
            "survey_root": str(survey_root) if survey_root else metadata.get("survey_root"),
            "queries": metadata.get("queries"),
        }
    )
    write_json(output_path, report)
    print(json.dumps({k: v for k, v in report.items() if k != "ranked_candidates"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
