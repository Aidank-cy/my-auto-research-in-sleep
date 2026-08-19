#!/usr/bin/env python3
"""Merge duplicate research-lit candidates without dropping fields.

The tool reads search/candidate_metadata.json, groups candidates that appear to
be the same paper, merges their fields, and rewrites candidate_metadata.json plus
candidate_papers.json. It is local-only: no network calls, no scoring.

Examples
--------
python3 tools/research_candidate_dedupe.py \
  --topic-name agent-memory

python3 tools/research_candidate_dedupe.py \
  --metadata /tmp/candidate_metadata.json \
  --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402

ARXIV_ID_RE = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_missing(value: Any) -> bool:
    return value in (None, "", [], {})


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    return text or None


def normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) >= 8 else None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if "/abs/" in text:
        text = text.split("/abs/", 1)[1]
    if "/pdf/" in text:
        text = text.split("/pdf/", 1)[1].removesuffix(".pdf")
    text = text.removeprefix("ARXIV:").removeprefix("arxiv:").removeprefix("id:")
    match = ARXIV_ID_RE.search(text)
    if not match:
        return None
    return re.sub(r"v\d+$", "", match.group(0))


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    text = text.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    text = text.removeprefix("doi.org/")
    match = DOI_RE.search(text)
    return match.group(0).lower() if match else (text or None)


def unique_append(items: list[Any], value: Any) -> None:
    if is_missing(value):
        return
    if not any(item == value for item in items):
        items.append(copy.deepcopy(value))


def add_preserved_value(candidate: dict[str, Any], path: str, value: Any) -> None:
    values = candidate.setdefault("merged_values", {})
    if not isinstance(values, dict):
        values = {"merged_values": values}
        candidate["merged_values"] = values
    bucket = values.setdefault(path, [])
    if not isinstance(bucket, list):
        bucket = [bucket]
        values[path] = bucket
    unique_append(bucket, value)


def merge_lists(existing: list[Any], incoming: list[Any]) -> bool:
    changed = False
    for item in incoming:
        before = len(existing)
        unique_append(existing, item)
        changed = changed or len(existing) != before
    return changed


def merge_dicts(existing: dict[str, Any], incoming: dict[str, Any], *, root: dict[str, Any], path: str) -> bool:
    changed = False
    for key, value in incoming.items():
        if is_missing(value):
            continue
        child_path = f"{path}.{key}" if path else key
        if key not in existing or is_missing(existing.get(key)):
            existing[key] = copy.deepcopy(value)
            changed = True
            continue
        changed = merge_value(existing, key, value, root=root, path=child_path) or changed
    return changed


def merge_value(target: dict[str, Any], key: str, value: Any, *, root: dict[str, Any], path: str) -> bool:
    if is_missing(value):
        return False
    current = target.get(key)
    if is_missing(current):
        target[key] = copy.deepcopy(value)
        return True
    if current == value:
        return False
    if isinstance(current, list) and isinstance(value, list):
        return merge_lists(current, value)
    if isinstance(current, dict) and isinstance(value, dict):
        return merge_dicts(current, value, root=root, path=path)

    add_preserved_value(root, path, value)
    return True


def merge_candidate(base: dict[str, Any], incoming: dict[str, Any]) -> bool:
    changed = False
    incoming_id = incoming.get("id")
    if incoming_id and incoming_id != base.get("id"):
        merged_ids = base.setdefault("merged_ids", [])
        before = len(merged_ids)
        unique_append(merged_ids, incoming_id)
        changed = changed or len(merged_ids) != before

    for key, value in incoming.items():
        if key == "id":
            continue
        changed = merge_value(base, key, value, root=base, path=key) or changed
    return changed


def external_ids(candidate: dict[str, Any]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict):
        nested = semantic_scholar.get("externalIds")
        if isinstance(nested, dict):
            ids.update(nested)

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        s2_payload = source_payloads.get("semantic-scholar")
        if isinstance(s2_payload, dict):
            nested = s2_payload.get("externalIds")
            if isinstance(nested, dict):
                ids.update(nested)
    return ids


def candidate_keys(candidate: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict) and semantic_scholar.get("paperId"):
        keys.append(f"s2:{semantic_scholar['paperId']}")

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        s2_payload = source_payloads.get("semantic-scholar")
        if isinstance(s2_payload, dict) and s2_payload.get("paperId"):
            keys.append(f"s2:{s2_payload['paperId']}")
        arxiv_payload = source_payloads.get("arxiv")
        if isinstance(arxiv_payload, dict):
            arxiv_id = normalize_arxiv_id(clean_text(arxiv_payload.get("id")))
            if arxiv_id:
                keys.append(f"arxiv:{arxiv_id}")
        hf_payload = source_payloads.get("huggingface-papers")
        if isinstance(hf_payload, dict):
            arxiv_id = normalize_arxiv_id(clean_text(hf_payload.get("id")))
            if arxiv_id:
                keys.append(f"arxiv:{arxiv_id}")

    arxiv_id = normalize_arxiv_id(clean_text(candidate.get("arxiv_id")))
    if arxiv_id:
        keys.append(f"arxiv:{arxiv_id}")
    doi = normalize_doi(clean_text(candidate.get("doi")))
    if doi:
        keys.append(f"doi:{doi}")

    ids = external_ids(candidate)
    ext_arxiv = normalize_arxiv_id(clean_text(ids.get("ArXiv") or ids.get("arxiv")))
    if ext_arxiv:
        keys.append(f"arxiv:{ext_arxiv}")
    ext_doi = normalize_doi(clean_text(ids.get("DOI") or ids.get("doi")))
    if ext_doi:
        keys.append(f"doi:{ext_doi}")

    title = normalize_title(clean_text(candidate.get("title")))
    if title:
        keys.append(f"title:{title}")

    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return
        if root_b < root_a:
            root_a, root_b = root_b, root_a
        self.parent[root_b] = root_a


def build_groups(candidates: list[dict[str, Any]]) -> list[list[int]]:
    uf = UnionFind(len(candidates))
    seen: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        for key in candidate_keys(candidate):
            if key in seen:
                uf.union(index, seen[key])
            else:
                seen[key] = index

    groups_by_root: dict[int, list[int]] = {}
    for index in range(len(candidates)):
        groups_by_root.setdefault(uf.find(index), []).append(index)
    return sorted(groups_by_root.values(), key=lambda group: group[0])


def minimal_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate.get("id"),
        "arxiv_id": candidate.get("arxiv_id"),
        "doi": candidate.get("doi"),
        "title": candidate.get("title"),
        "verification_status": candidate.get("verification_status"),
        "verification_method": candidate.get("verification_method"),
    }


def refresh_source_status(search_dir: Path, candidates: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    source_status_path = search_dir / "source_status.json"
    if not source_status_path.exists():
        return None
    payload = json.loads(source_status_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return None

    for status in payload:
        if not isinstance(status, dict):
            continue
        source = status.get("source")
        if not source:
            continue
        has_current_run_provenance = any("current_run_sources" in candidate for candidate in candidates)
        current_count = (
            sum(1 for candidate in candidates if source in (candidate.get("current_run_sources") or []))
            if has_current_run_provenance
            else int(status.get("current_run_usable_candidate_count", status.get("usable_candidate_count", 0)) or 0)
        )
        if not status.get("succeeded"):
            current_count = 0
        status["usable_candidate_count"] = current_count
        status["current_run_usable_candidate_count"] = current_count
        status["historical_candidate_count"] = sum(
            1 for candidate in candidates if source in (candidate.get("sources") or [])
        )
    write_json(source_status_path, payload)
    return payload


def dedupe_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups = build_groups(candidates)
    merged: list[dict[str, Any]] = []
    merged_groups: list[dict[str, Any]] = []

    for group in groups:
        base = copy.deepcopy(candidates[group[0]])
        before_keys = candidate_keys(base)
        for index in group[1:]:
            merge_candidate(base, candidates[index])
        merged.append(base)
        if len(group) > 1:
            merged_groups.append(
                {
                    "canonical_id": base.get("id"),
                    "merged_ids": [candidates[index].get("id") for index in group[1:]],
                    "keys": before_keys,
                }
            )

    report = {
        "input_count": len(candidates),
        "output_count": len(merged),
        "merged_count": len(candidates) - len(merged),
        "groups": merged_groups,
    }
    return merged, report


def load_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidate metadata must be an object with a candidates list")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument("--metadata", type=Path, help="Explicit candidate_metadata.json path.")
    parser.add_argument("--candidate-papers", type=Path, help="candidate_papers.json path; default next to metadata.")
    parser.add_argument("--report", type=Path, help="Output report path; default search/candidate_dedupe.json.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing files.")
    return parser


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    survey_root = resolve_survey_root(args.survey_root, args.topic_name, required=False)
    if args.metadata:
        metadata_path = args.metadata
    elif survey_root:
        metadata_path = survey_root / "search" / "candidate_metadata.json"
    else:
        raise ValueError("provide --topic-name, --survey-root, or --metadata")

    candidate_papers_path = args.candidate_papers or (metadata_path.parent / "candidate_papers.json")
    report_path = args.report or (metadata_path.parent / "candidate_dedupe.json")
    return metadata_path, candidate_papers_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata_path, candidate_papers_path, report_path = resolve_paths(args)
        metadata = load_metadata(metadata_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    deduped, report = dedupe_candidates(metadata["candidates"])
    search_dir = metadata_path.parent
    search_summary_path = metadata_path.parent / "search_summary.json"
    source_status_path = metadata_path.parent / "source_status.json"
    report.update(
        {
            "metadata_path": str(metadata_path),
            "candidate_papers_path": str(candidate_papers_path),
            "search_summary_path": str(search_summary_path) if search_summary_path.exists() else None,
            "source_status_path": str(source_status_path) if source_status_path.exists() else None,
            "dry_run": args.dry_run,
        }
    )

    if not args.dry_run:
        metadata["candidates"] = deduped
        write_json(metadata_path, metadata)
        verified = [
            candidate for candidate in deduped
            if candidate.get("verification_status") == "verified"
        ]
        write_json(candidate_papers_path, [minimal_candidate(candidate) for candidate in verified])
        source_status = refresh_source_status(search_dir, deduped)
        if search_summary_path.exists():
            summary = json.loads(search_summary_path.read_text(encoding="utf-8"))
            if isinstance(summary, dict):
                summary["candidate_count"] = len(deduped)
                summary["verified_candidate_count"] = len(verified)
                summary["no_verified_evidence"] = bool(deduped) and not verified
                if source_status is not None:
                    summary["source_status"] = source_status
                    summary["no_scriptable_evidence"] = not any(
                        isinstance(status, dict)
                        and status.get("source") != "web-fallback"
                        and int(status.get("usable_candidate_count") or 0) > 0
                        for status in source_status
                    )
                write_json(search_summary_path, summary)
        write_json(report_path, report)

    print(json.dumps({k: v for k, v in report.items() if k != "groups"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
