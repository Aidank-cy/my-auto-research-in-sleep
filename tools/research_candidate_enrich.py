#!/usr/bin/env python3
"""Run research-lit candidate enrichment stages."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_candidate_citation_enrich as citation_enrich  # noqa: E402
import research_candidate_dedupe as candidate_dedupe  # noqa: E402
import research_candidate_metadata_hydrate as metadata_hydrate  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_step(name: str, fn: Any, argv: list[str]) -> dict[str, Any]:
    started = time.time()
    code = fn(argv)
    return {
        "name": name,
        "exit_code": code,
        "elapsed_seconds": round(time.time() - started, 2),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument(
        "--stage",
        choices=("pre-relevance", "citation"),
        default="pre-relevance",
        help="pre-relevance runs dedupe plus cheap metadata; citation runs post-relevance citation fill.",
    )
    parser.add_argument("--hf-upvote-threshold", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, help="Pass through to metadata hydration and citation enrichment.")
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--skip-arxiv", action="store_true", help="Pass through to metadata hydration.")
    return parser


def step_args(args: argparse.Namespace) -> tuple[list[str], list[str], list[str]]:
    base = ["--survey-root", str(args.survey_root)]
    metadata_args = base + ["--sleep", str(args.sleep), "--skip-s2"]
    dedupe_args = list(base)
    citation_args = base + [
        "--hf-upvote-threshold",
        str(args.hf_upvote_threshold),
        "--sleep",
        str(args.sleep),
        "--positive-relevance-only",
    ]
    if args.limit is not None:
        metadata_args += ["--limit", str(args.limit)]
        citation_args += ["--limit", str(args.limit)]
    if args.dry_run:
        metadata_args.append("--dry-run")
        dedupe_args.append("--dry-run")
        citation_args.append("--dry-run")
    if args.skip_arxiv:
        metadata_args.append("--skip-arxiv")
    return metadata_args, dedupe_args, citation_args


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.survey_root = resolve_survey_root(args.survey_root, args.topic_name)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    metadata_args, dedupe_args, citation_args = step_args(args)
    if args.stage == "pre-relevance":
        steps = [
            ("candidate_deduplication", candidate_dedupe.main, dedupe_args),
            ("cheap_metadata_hydration", metadata_hydrate.main, metadata_args),
        ]
    else:
        steps = [("citation_enrichment", citation_enrich.main, citation_args)]

    records = []
    for name, fn, step_argv in steps:
        record = run_step(name, fn, step_argv)
        records.append(record)
        if record["exit_code"] != 0:
            break

    report = {
        "survey_root": str(args.survey_root),
        "stage": args.stage,
        "dry_run": args.dry_run,
        "hf_upvote_threshold": args.hf_upvote_threshold,
        "steps": records,
    }
    if not args.dry_run:
        write_json(args.survey_root / "search" / "candidate_enrichment.json", report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return next((step["exit_code"] for step in records if step["exit_code"] != 0), 0)


if __name__ == "__main__":
    raise SystemExit(main())
