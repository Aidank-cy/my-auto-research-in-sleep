#!/usr/bin/env python3
"""Plan which research-lit papers should become survey paper/meeting notes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_artifact_io import write_json  # noqa: E402
from research_packet_io import load_packet_bundle  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402
import research_synthesis_compile  # noqa: E402


PROMOTABLE_EVIDENCE_LEVELS = {"local-note", "fulltext"}
METADATA_ONLY_LEVELS = {"metadata-only", "metadata_only", "metadata"}
EVIDENCE_LEVEL_ALIASES = {
    "local_note": "local-note",
    "metadata": "metadata-only",
    "metadata_only": "metadata-only",
    "full-text": "fulltext",
}
RELATION_WEIGHTS = {
    "addresses_gap": 45,
    "contradicts": 40,
    "extends": 30,
    "supersedes": 30,
    "inspired_by": 25,
    "tested_by": 10,
    "supports": 10,
    "invalidates": 10,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    return load_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        records.append(record)
    return records


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_evidence_level(value: Any) -> str:
    text = clean_text(value).lower()
    return EVIDENCE_LEVEL_ALIASES.get(text, text)


def normalize_paper_id(value: str) -> str:
    text = clean_text(value)
    return text.removeprefix("paper:")


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_candidates(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_optional_json(path)
    records = payload.get("candidates") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return {}
    return {clean_text(record.get("id")): record for record in records if isinstance(record, dict) and clean_text(record.get("id"))}


def load_ranking(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_optional_json(path)
    records = payload.get("ranked_candidates") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return {}
    return {clean_text(record.get("id")): record for record in records if isinstance(record, dict) and clean_text(record.get("id"))}


def load_packets(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    _, records = load_packet_bundle(path)
    return {clean_text(record.get("id")): record for record in records if isinstance(record, dict) and clean_text(record.get("id"))}


def load_wiki_notes(path: Path) -> dict[str, dict[str, Any]]:
    records = load_optional_json(path)
    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_id = clean_text(record.get("id"))
        if raw_id:
            result[normalize_paper_id(raw_id)] = record
    return result


def relevance_value(candidate: dict[str, Any], paper: dict[str, Any]) -> Any:
    if "relevance" in candidate:
        return candidate.get("relevance")
    return paper.get("relevance")


def is_relevant(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    text = clean_text(value).lower()
    if text in {"1", "true", "yes", "relevant"}:
        return True
    if text in {"0", "false", "no", "irrelevant"}:
        return False
    return None


def rank_value(ranking: dict[str, Any], paper: dict[str, Any]) -> int:
    raw = ranking.get("rank", paper.get("rank"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 10**9


def title_value(candidate: dict[str, Any], paper: dict[str, Any], packet: dict[str, Any]) -> str:
    return clean_text(candidate.get("title")) or clean_text(paper.get("title")) or clean_text(packet.get("title"))


def slugify(title: str, fallback: str) -> str:
    text = title.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:96].strip("-") or fallback


def note_path_for(survey_root: Path, paper_id: str, title: str) -> str:
    slug = slugify(title, paper_id)
    return str(survey_root / "notes" / "papers" / f"{slug}.md")


def evidence_source_papers(records: list[dict[str, Any]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for record in records:
        node_id = clean_text(record.get("node_id"))
        match = re.match(r"^paper:([^;\s]+)(?:;|$)", clean_text(record.get("provenance")))
        if node_id and match:
            sources[node_id] = match.group(1)
    return sources


def evidence_nodes_by_paper(records: list[dict[str, Any]], source_papers: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for node_id in as_list(record.get("evidence_nodes")):
            paper_id = source_papers.get(clean_text(node_id), "")
            if paper_id:
                counts[paper_id] = counts.get(paper_id, 0) + 1
    return counts


def relation_score_by_paper(
    relations: list[dict[str, Any]],
    evidence_sources: dict[str, str],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    scores: dict[str, int] = {}
    summaries: dict[str, list[str]] = {}

    def add(paper_id: str, score: int, summary: str) -> None:
        if not paper_id:
            return
        scores[paper_id] = scores.get(paper_id, 0) + score
        summaries.setdefault(paper_id, [])
        if summary not in summaries[paper_id]:
            summaries[paper_id].append(summary)

    for relation in relations:
        relation_type = clean_text(relation.get("type"))
        weight = RELATION_WEIGHTS.get(relation_type, 5)
        source = clean_text(relation.get("from"))
        target = clean_text(relation.get("to"))
        summary = f"{source} --{relation_type}--> {target}"
        if source.startswith("paper:"):
            add(normalize_paper_id(source), weight, summary)
        if target.startswith("paper:"):
            add(normalize_paper_id(target), weight, summary)
        for node_id in as_list(relation.get("source_nodes")):
            text = clean_text(node_id)
            if text.startswith("paper:"):
                add(normalize_paper_id(text), max(5, weight // 2), summary)
            elif text in evidence_sources:
                add(evidence_sources[text], max(5, weight // 2), summary)
        for packet_id in as_list(relation.get("source_packets")):
            add(normalize_paper_id(clean_text(packet_id)), max(3, weight // 3), summary)
    return scores, summaries


def existing_note_path(
    *,
    candidate: dict[str, Any],
    paper: dict[str, Any],
    wiki_note: dict[str, Any] | None,
    planned_path: str,
) -> str:
    for value in (
        candidate.get("local_note_path"),
        paper.get("local_note_path"),
        wiki_note.get("note_path") if wiki_note else None,
    ):
        text = clean_text(value)
        if text:
            return text
    return planned_path if Path(planned_path).exists() else ""


def build_candidate_entry(
    *,
    survey_root: Path,
    paper: dict[str, Any],
    candidate: dict[str, Any],
    ranking: dict[str, Any],
    packet: dict[str, Any],
    wiki_note: dict[str, Any] | None,
    forced_ids: set[str],
    allow_metadata_only: bool,
    allow_missing_relevance: bool,
    claim_counts: dict[str, int],
    problem_counts: dict[str, int],
    relation_scores: dict[str, int],
    relation_summaries: dict[str, list[str]],
) -> dict[str, Any]:
    node_id = clean_text(paper.get("node_id"))
    paper_id = clean_text(paper.get("paper_id")) or normalize_paper_id(node_id)
    title = title_value(candidate, paper, packet)
    evidence_level = normalize_evidence_level(packet.get("evidence_level") or paper.get("evidence_level"))
    read_scope = clean_text(packet.get("read_scope"))
    relevance = relevance_value(candidate, paper)
    relevant = is_relevant(relevance)
    forced = paper_id in forced_ids
    rank = rank_value(ranking, paper)
    planned_path = note_path_for(survey_root, paper_id, title)
    existing_path = existing_note_path(candidate=candidate, paper=paper, wiki_note=wiki_note, planned_path=planned_path)

    skip_reasons: list[str] = []
    reasons: list[str] = []
    score = 0

    if relevant is False and not forced:
        skip_reasons.append("relevance_not_1")
    elif relevant is None and not forced and not allow_missing_relevance:
        skip_reasons.append("relevance_missing")
    else:
        score += 30
        reasons.append("relevance=1" if relevant is True else "forced_or_missing_relevance_allowed")

    if evidence_level in METADATA_ONLY_LEVELS and not forced and not allow_metadata_only:
        skip_reasons.append("metadata_only")
    elif evidence_level in PROMOTABLE_EVIDENCE_LEVELS:
        evidence_score = {"local-note": 25, "fulltext": 35}.get(evidence_level, 0)
        score += evidence_score
        reasons.append(f"promotable_evidence:{evidence_level}")
    elif evidence_level in METADATA_ONLY_LEVELS:
        score -= 20
        reasons.append("metadata_only_allowed")
    else:
        skip_reasons.append("evidence_level_missing_or_unknown")

    if rank < 10**9:
        rank_score = max(0, 30 - min(rank, 30))
        score += rank_score
        reasons.append(f"rank:{rank}")

    if existing_path:
        score += 25
        reasons.append("existing_or_local_note")

    claim_count = claim_counts.get(paper_id, 0)
    problem_count = problem_counts.get(paper_id, 0)
    relation_score = relation_scores.get(paper_id, 0)
    if claim_count:
        score += claim_count * 15
        reasons.append(f"supports_claims:{claim_count}")
    if problem_count:
        score += problem_count * 20
        reasons.append(f"supports_problems:{problem_count}")
    if relation_score:
        score += relation_score
        reasons.append(f"relation_centrality:{relation_score}")

    if forced:
        score += 1000
        reasons.append("forced_include")

    note_action = "reuse_existing_note" if existing_path else "write_survey_note"
    return {
        "paper_id": paper_id,
        "node_id": node_id or f"paper:{paper_id}",
        "title": title,
        "rank": None if rank == 10**9 else rank,
        "relevance": relevance,
        "evidence_level": evidence_level,
        "read_scope": read_scope,
        "score": score,
        "eligible": not skip_reasons,
        "skip_reasons": skip_reasons,
        "reasons": reasons,
        "claim_support_count": claim_count,
        "problem_support_count": problem_count,
        "relation_score": relation_score,
        "relation_summaries": relation_summaries.get(paper_id, [])[:5],
        "note_action": note_action,
        "note_path": existing_path or planned_path,
        "planned_note_path": planned_path,
        "existing_note_path": existing_path or None,
    }


def build_plan(
    survey_root: Path,
    *,
    max_notes: int = 12,
    allow_metadata_only: bool = False,
    allow_missing_relevance: bool = False,
    include_paper_ids: list[str] | None = None,
) -> dict[str, Any]:
    if max_notes < 0:
        raise ValueError("max_notes must be >= 0")

    forced_ids = {normalize_paper_id(item) for item in include_paper_ids or []}
    synthesis_dir = survey_root / "synthesis"
    logic_dir = synthesis_dir / "logic"
    evidence_dir = synthesis_dir / "evidence"
    candidates = load_candidates(survey_root / "search" / "candidate_metadata.json")
    ranking = load_ranking(survey_root / "search" / "candidate_ranking.json")
    manifest = load_optional_json(synthesis_dir / "manifest.json")
    stages = manifest.get("stages") if isinstance(manifest, dict) else {}
    for stage in ("projections", "relations"):
        if not isinstance(stages, dict) or stages.get(stage, {}).get("status") != "valid":
            raise ValueError(f"synthesis stage is not valid: {stage}")
    packets = load_packets(synthesis_dir / "packets" / "index.json")
    wiki_notes = load_wiki_notes(synthesis_dir / "wiki_notes.json")
    papers_path = evidence_dir / "papers.jsonl"
    if not papers_path.exists():
        raise FileNotFoundError(f"required paper nodes file not found: {papers_path}")
    papers = read_jsonl(papers_path)
    evidence = read_jsonl(evidence_dir / "evidence.jsonl")
    claims = read_jsonl(logic_dir / "claims.jsonl")
    problems = read_jsonl(logic_dir / "problems.jsonl")
    relations = read_jsonl(logic_dir / "relations.jsonl")

    source_papers = evidence_source_papers(evidence)
    claim_counts = evidence_nodes_by_paper(claims, source_papers)
    problem_counts = evidence_nodes_by_paper(problems, source_papers)
    relation_scores, relation_summaries = relation_score_by_paper(relations, source_papers)

    entries: list[dict[str, Any]] = []
    for paper in papers:
        paper_id = clean_text(paper.get("paper_id")) or normalize_paper_id(clean_text(paper.get("node_id")))
        if not paper_id:
            continue
        entries.append(
            build_candidate_entry(
                survey_root=survey_root,
                paper=paper,
                candidate=candidates.get(paper_id, {}),
                ranking=ranking.get(paper_id, {}),
                packet=packets.get(paper_id, {}),
                wiki_note=wiki_notes.get(paper_id),
                forced_ids=forced_ids,
                allow_metadata_only=allow_metadata_only,
                allow_missing_relevance=allow_missing_relevance,
                claim_counts=claim_counts,
                problem_counts=problem_counts,
                relation_scores=relation_scores,
                relation_summaries=relation_summaries,
            )
        )

    selected = sorted(
        (entry for entry in entries if entry["eligible"]),
        key=lambda item: (-int(item["score"]), item["rank"] if item["rank"] is not None else 10**9, item["paper_id"]),
    )[:max_notes]
    selected_ids = {entry["paper_id"] for entry in selected}
    skipped = sorted(
        (entry for entry in entries if entry["paper_id"] not in selected_ids),
        key=lambda item: (item["rank"] if item["rank"] is not None else 10**9, item["paper_id"]),
    )

    return {
        "survey_root": str(survey_root),
        "max_notes": max_notes,
        "allow_metadata_only": allow_metadata_only,
        "allow_missing_relevance": allow_missing_relevance,
        "forced_paper_ids": sorted(forced_ids),
        "selection_policy": {
            "default_boundary": "top 8-12 relevant, promotable-evidence papers; metadata-only excluded unless explicitly allowed",
            "promotable_evidence_levels": sorted(PROMOTABLE_EVIDENCE_LEVELS),
            "ranking_priority": [
                "forced include",
                "relevance=1",
                "local-note/fulltext evidence",
                "claim/problem support",
                "ARIS-compatible relation involvement",
                "existing local note reuse",
                "candidate rank",
            ],
        },
        "selected_count": len(selected),
        "selected": selected,
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing synthesis artifacts.")
    parser.add_argument("--max-notes", type=int, default=12)
    parser.add_argument("--allow-metadata-only", action="store_true")
    parser.add_argument("--allow-missing-relevance", action="store_true")
    parser.add_argument("--include-paper-id", action="append", default=[], help="Force-include a candidate id, e.g. p31.")
    parser.add_argument("--output", type=Path, help="Default: <survey-root>/synthesis/wiki_promotion_plan.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
        manifest = load_json(survey_root / "synthesis" / "manifest.json")
        current_errors = research_synthesis_compile.audit_current(survey_root, manifest)
        if current_errors:
            raise ValueError(f"synthesis artifacts are stale: {current_errors[0]['message']}")
        plan = build_plan(
            survey_root,
            max_notes=args.max_notes,
            allow_metadata_only=args.allow_metadata_only,
            allow_missing_relevance=args.allow_missing_relevance,
            include_paper_ids=args.include_paper_id,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    output_path = args.output or (survey_root / "synthesis" / "wiki_promotion_plan.json")
    write_json(output_path, plan)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "selected_count": plan["selected_count"],
                "skipped_count": plan["skipped_count"],
                "max_notes": plan["max_notes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
