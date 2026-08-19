#!/usr/bin/env python3
"""Fetch, cache, gate, and materialize Semantic Scholar references.

This is a compatibility helper for the former reference-coverage workflow and
is not called by the current three-stage ``research-lit`` controller.
It keeps API failure distinct from a successful empty reference list, resumes
only missing/retryable records, blocks scoring below the configured coverage,
imports valid prior-run caches, and copies successful pre-score cache files
into selected paper folders.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from . import semantic_scholar_fetch as s2
else:
    import semantic_scholar_fetch as s2


SUCCESS_STATUSES = {"success_nonempty", "success_empty"}
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
PAUSED_EXIT_CODE = 75
ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})(?:v\d+)?$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _paper_id(paper: dict[str, Any]) -> str:
    value = paper.get("id") or paper.get("paper_id")
    if not value:
        raise ValueError("candidate/selected paper is missing id or paper_id")
    return str(value)


def _semantic_scholar_lookup_id(paper_id: str) -> str:
    return f"ARXIV:{paper_id}" if ARXIV_ID_RE.fullmatch(paper_id) else paper_id


def _cache_path(cache_dir: Path, paper_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", paper_id)
    return cache_dir / f"{safe}.json"


def _normalize_external_ids(entries: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        external = entry.get("externalIds") or {}
        value = f"arXiv:{external['ArXiv']}" if external.get("ArXiv") else external.get("DOI")
        normalized = str(value) if value else None
        if normalized and normalized not in seen:
            values.append(normalized)
            seen.add(normalized)
    return values


def _http_status(message: str) -> int | None:
    match = re.search(r"HTTP (\d{3})", message)
    return int(match.group(1)) if match else None


def _classify_failure(message: str) -> tuple[str, int | None]:
    code = _http_status(message)
    if code == 404:
        return "not_found", code
    if code in RETRYABLE_HTTP_CODES or message.startswith("Network error:"):
        return "retryable_failure", code
    return "error", code


def _load_state(path: Path) -> dict[str, Any]:
    state = _read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("papers", {})
    return state


def _cache_is_valid(path: Path) -> bool:
    try:
        return isinstance(_read_json(path, None), list)
    except (OSError, json.JSONDecodeError):
        return False


def _seed_from_paper_folder(
    paper: dict[str, Any], cache_path: Path, previous: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Reuse a durable references.json from an already-analyzed paper."""
    folder = paper.get("paper_folder")
    if not folder:
        return None
    source = Path(str(folder)) / "references.json"
    if not _cache_is_valid(source):
        return None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, cache_path)
    entries = _read_json(source, [])
    return {
        "status": "success_nonempty" if entries else "success_empty",
        "attempts": int((previous or {}).get("attempts", 0)),
        "http_status": None,
        "reference_count": len(entries),
        "cache_path": str(cache_path),
        "error": None,
        "source": "existing_paper_folder",
        "updated_at": _now(),
    }


def _backfill_paper_folder(paper: dict[str, Any], cache_path: Path) -> None:
    """Persist a successful rerun fetch beside an existing analyzed paper."""
    folder = paper.get("paper_folder")
    if not folder or not _cache_is_valid(cache_path):
        return
    destination = Path(str(folder)) / "references.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() != cache_path.resolve():
        shutil.copyfile(cache_path, destination)


def _fetch_one(paper_id: str, cache_dir: Path, previous: dict[str, Any] | None, limit: int) -> dict[str, Any]:
    cache_path = _cache_path(cache_dir, paper_id)
    attempts = int((previous or {}).get("attempts", 0)) + 1
    try:
        entries = s2.get_references(_semantic_scholar_lookup_id(paper_id), limit=limit)
    except Exception as exc:
        message = str(exc)
        status, http_status = _classify_failure(message)
        return {
            "status": status,
            "attempts": attempts,
            "http_status": http_status,
            "reference_count": None,
            "cache_path": None,
            "error": message,
            "updated_at": _now(),
        }

    _write_json(cache_path, entries)
    return {
        "status": "success_nonempty" if entries else "success_empty",
        "attempts": attempts,
        "http_status": 200,
        "reference_count": len(entries),
        "cache_path": str(cache_path),
        "error": None,
        "updated_at": _now(),
    }


def _summarize(state: dict[str, Any], paper_ids: list[str], threshold: float) -> dict[str, Any]:
    records = state.get("papers", {})
    counts: dict[str, int] = {}
    for paper_id in paper_ids:
        status = (records.get(paper_id) or {}).get("status", "missing")
        counts[status] = counts.get(status, 0) + 1
    successes = sum(counts.get(status, 0) for status in SUCCESS_STATUSES)
    total = len(paper_ids)
    coverage = successes / total if total else 1.0
    return {
        "total": total,
        "successful": successes,
        "coverage": round(coverage, 6),
        "coverage_threshold": threshold,
        "counts": counts,
        "ready_for_scoring": coverage >= threshold,
    }


def import_prior_caches(
    candidates_path: Path,
    source_dirs: list[Path],
    cache_dir: Path,
    status_path: Path,
    *,
    coverage_threshold: float,
) -> dict[str, Any]:
    """Import valid same-topic cache files without treating them as fresh API calls."""
    candidates = _read_json(candidates_path, [])
    if not isinstance(candidates, list):
        raise ValueError("candidates JSON must be a list")
    state = _load_state(status_path)
    records = state["papers"]
    imported = reused = missing = 0
    for paper in candidates:
        pid = _paper_id(paper)
        destination = _cache_path(cache_dir, pid)
        record = records.get(pid) or {}
        if record.get("status") in SUCCESS_STATUSES and _cache_is_valid(destination):
            reused += 1
            continue
        source = next(
            (directory / destination.name for directory in source_dirs if _cache_is_valid(directory / destination.name)),
            None,
        )
        if source is None:
            missing += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        entries = _read_json(destination, [])
        records[pid] = {
            "status": "success_nonempty" if entries else "success_empty",
            "attempts": int(record.get("attempts", 0)),
            "http_status": None,
            "reference_count": len(entries),
            "cache_path": str(destination),
            "error": None,
            "source": "prior_same_topic_run",
            "source_cache_path": str(source),
            "updated_at": _now(),
        }
        imported += 1
    paper_ids = [_paper_id(item) for item in candidates]
    state["updated_at"] = _now()
    state["summary"] = _summarize(state, paper_ids, coverage_threshold)
    _write_json(status_path, state)
    return {"candidates": len(candidates), "imported": imported, "reused": reused, "missing": missing}


def fetch_candidates(
    candidates_path: Path,
    cache_dir: Path,
    status_path: Path,
    *,
    limit: int,
    coverage_threshold: float,
    retry_not_found: bool = False,
) -> tuple[dict[str, Any], int]:
    candidates = _read_json(candidates_path, [])
    if not isinstance(candidates, list):
        raise ValueError("candidates JSON must be a list")
    state = _load_state(status_path)
    records = state["papers"]

    for paper in candidates:
        paper_id = _paper_id(paper)
        previous = records.get(paper_id)
        previous_status = (previous or {}).get("status")
        cached = _cache_path(cache_dir, paper_id)
        if previous_status in SUCCESS_STATUSES and _cache_is_valid(cached):
            record = previous
        elif previous_status == "not_found" and not retry_not_found:
            record = previous
        else:
            record = _seed_from_paper_folder(paper, cached, previous)
            if record is None:
                record = _fetch_one(paper_id, cache_dir, previous, limit)
            records[paper_id] = record

        if record.get("status") in SUCCESS_STATUSES and _cache_is_valid(cached):
            entries = _read_json(cached, [])
            paper["references"] = _normalize_external_ids(entries)
            _backfill_paper_folder(paper, cached)
        else:
            # Keep the stable list shape for downstream consumers, while the
            # adjacent status field preserves unknown vs genuine emptiness.
            paper["references"] = []
        paper["reference_fetch_status"] = record.get("status")
        paper["reference_fetch_attempts"] = record.get("attempts", 0)
        _write_json(candidates_path, candidates)
        state["updated_at"] = _now()
        state["summary"] = _summarize(state, [_paper_id(item) for item in candidates], coverage_threshold)
        _write_json(status_path, state)

    summary = _summarize(state, [_paper_id(item) for item in candidates], coverage_threshold)
    state["updated_at"] = _now()
    state["summary"] = summary
    state["pipeline_status"] = "ready_for_scoring" if summary["ready_for_scoring"] else "paused_waiting_for_references"
    _write_json(status_path, state)
    return summary, 0 if summary["ready_for_scoring"] else PAUSED_EXIT_CODE


def materialize_selected(
    selected_path: Path,
    cache_dir: Path,
    status_path: Path,
    *,
    limit: int,
    retry_missing: bool,
) -> dict[str, Any]:
    selected = _read_json(selected_path, [])
    if not isinstance(selected, list):
        raise ValueError("selected JSON must be a list")
    state = _load_state(status_path)
    records = state["papers"]
    copied = retried = missing = 0

    for paper in selected:
        paper_id = _paper_id(paper)
        folder_value = paper.get("paper_folder")
        if not folder_value:
            missing += 1
            continue
        cached = _cache_path(cache_dir, paper_id)
        record = records.get(paper_id)
        retryable_status = (record or {}).get("status") in {None, "retryable_failure", "error"}
        invalid_success_cache = (record or {}).get("status") in SUCCESS_STATUSES and not _cache_is_valid(cached)
        if retry_missing and (retryable_status or invalid_success_cache):
            retried += 1
            record = _fetch_one(paper_id, cache_dir, record, limit)
            records[paper_id] = record
            state["updated_at"] = _now()
            _write_json(status_path, state)
        if (record or {}).get("status") in SUCCESS_STATUSES and _cache_is_valid(cached):
            destination = Path(str(folder_value)) / "references.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, destination)
            copied += 1
        else:
            missing += 1

    result = {"selected": len(selected), "copied": copied, "retried": retried, "missing": missing}
    state["updated_at"] = _now()
    state["materialization"] = result
    _write_json(status_path, state)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and reuse Semantic Scholar reference caches.")
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="Fetch/resume candidate references and enforce coverage")
    fetch.add_argument("--candidates", required=True)
    fetch.add_argument("--cache-dir", required=True)
    fetch.add_argument("--status", required=True)
    fetch.add_argument("--limit", type=int, default=500)
    fetch.add_argument("--coverage-threshold", type=float, default=0.95)
    fetch.add_argument("--retry-not-found", action="store_true")

    imported = commands.add_parser("import-cache", help="Import valid caches from prior same-topic runs")
    imported.add_argument("--candidates", required=True)
    imported.add_argument("--source-dir", action="append", required=True)
    imported.add_argument("--cache-dir", required=True)
    imported.add_argument("--status", required=True)
    imported.add_argument("--coverage-threshold", type=float, default=0.95)

    materialize = commands.add_parser("materialize", help="Copy candidate caches into selected paper folders")
    materialize.add_argument("--selected", required=True)
    materialize.add_argument("--cache-dir", required=True)
    materialize.add_argument("--status", required=True)
    materialize.add_argument("--limit", type=int, default=500)
    materialize.add_argument("--retry-missing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch":
        if not 0.0 <= args.coverage_threshold <= 1.0:
            raise ValueError("--coverage-threshold must be between 0 and 1")
        summary, exit_code = fetch_candidates(
            Path(args.candidates), Path(args.cache_dir), Path(args.status),
            limit=args.limit, coverage_threshold=args.coverage_threshold,
            retry_not_found=args.retry_not_found,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return exit_code
    if args.command == "import-cache":
        result = import_prior_caches(
            Path(args.candidates), [Path(value) for value in args.source_dir],
            Path(args.cache_dir), Path(args.status),
            coverage_threshold=args.coverage_threshold,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result = materialize_selected(
        Path(args.selected), Path(args.cache_dir), Path(args.status),
        limit=args.limit, retry_missing=args.retry_missing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
