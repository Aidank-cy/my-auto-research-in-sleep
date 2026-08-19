#!/usr/bin/env python3
"""Validate source packets and scaffold the survey synthesis workspace."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_candidate_rank  # noqa: E402
from research_artifact_io import atomic_write_text, sha256_file, sha256_text, write_json, write_jsonl  # noqa: E402
from research_packet_io import PACKET_INDEX_SCHEMA_VERSION, load_packet_bundle  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402


SCHEMA_VERSION = 4
VALID_EVIDENCE_LEVELS = {"fulltext", "local-note", "metadata-only"}
SOURCE_LEVELS = {"pdf": "fulltext", "local-note": "local-note", "metadata": "metadata-only"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def unique_records(payload: Any, *, list_key: str, label: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    records = payload.get(list_key) if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return [], [{"code": "invalid_schema", "message": f"{label} must contain {list_key} list"}]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append({"code": "not_object", "message": f"{label}[{index}] is not an object"})
            continue
        record_id = clean_text(record.get("id"))
        if not record_id:
            errors.append({"code": "missing_id", "message": f"{label}[{index}] is missing id"})
            continue
        if record_id in seen:
            errors.append({"code": "duplicate_id", "message": f"duplicate {label} id: {record_id}"})
            continue
        seen.add(record_id)
        result.append(record)
    return result, errors


def resolve_source_path(value: Any) -> Path | None:
    text = clean_text(value)
    if not text:
        return None
    path = Path(text)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, path]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def validate_inputs(
    metadata_path: Path,
    ranking_path: Path,
    packet_index_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    for label, path in (("candidate metadata", metadata_path), ("candidate ranking", ranking_path), ("packet index", packet_index_path)):
        if not path.exists():
            errors.append({"code": "missing_file", "message": f"{label} not found: {path}"})
    if errors:
        return {}, {}, {}, errors
    try:
        metadata = load_json(metadata_path)
        ranking = load_json(ranking_path)
        packet_payload, packet_records = load_packet_bundle(packet_index_path)
    except Exception as exc:
        return {}, {}, {}, [{"code": "invalid_json", "message": str(exc)}]

    candidates, candidate_errors = unique_records(metadata, list_key="candidates", label="candidate metadata")
    ranked, ranking_errors = unique_records(ranking, list_key="ranked_candidates", label="candidate ranking")
    packets, packet_errors = unique_records({"packets": packet_records}, list_key="packets", label="source packets")
    errors.extend(candidate_errors + ranking_errors + packet_errors)
    candidates_by_id = {clean_text(item["id"]): item for item in candidates}
    ranking_by_id = {clean_text(item["id"]): item for item in ranked}
    packets_by_id = {clean_text(item["id"]): item for item in packets}

    selected_ids = [clean_text(item["id"]) for item in ranked if item.get("selected") is True]
    if not selected_ids:
        errors.append({"code": "empty_selection", "message": "candidate ranking contains no selected papers"})
    packet_ids = list(packets_by_id)
    if packet_payload.get("schema_version") != PACKET_INDEX_SCHEMA_VERSION:
        errors.append(
            {
                "code": "invalid_packet_schema_version",
                "message": f"packet index schema_version must be {PACKET_INDEX_SCHEMA_VERSION}",
            }
        )
    if packet_payload.get("packet_count") != len(packets):
        errors.append({"code": "packet_count_mismatch", "message": "packet_count must equal the packets list length"})
    if packet_payload.get("selected_paper_ids") != packet_ids:
        errors.append({"code": "packet_id_index_mismatch", "message": "selected_paper_ids must equal packet IDs in order"})
    if set(selected_ids) != set(packet_ids):
        errors.append(
            {
                "code": "selection_packet_mismatch",
                "message": "selected ranking IDs and packet IDs must match exactly",
                "selected_ids": selected_ids,
                "packet_ids": packet_ids,
            }
        )
    for paper_id, packet in packets_by_id.items():
        prefix = f"packet {paper_id}"
        if paper_id not in candidates_by_id:
            errors.append({"code": "unknown_packet_id", "message": f"{prefix} is missing from candidate metadata"})
        if paper_id not in ranking_by_id:
            errors.append({"code": "unknown_packet_id", "message": f"{prefix} is missing from candidate ranking"})
        if clean_text(packet.get("status")) != "ok":
            errors.append({"code": "packet_not_ok", "message": f"{prefix} status must be ok"})
        source_kind = clean_text(packet.get("source_kind"))
        evidence_level = clean_text(packet.get("evidence_level"))
        if evidence_level not in VALID_EVIDENCE_LEVELS:
            errors.append({"code": "invalid_evidence_level", "message": f"{prefix} has invalid evidence_level"})
        if SOURCE_LEVELS.get(source_kind) != evidence_level:
            errors.append({"code": "source_level_mismatch", "message": f"{prefix} source_kind and evidence_level disagree"})
        read_scope = clean_text(packet.get("read_scope"))
        text = clean_text(packet.get("text"))
        if not read_scope:
            errors.append({"code": "missing_read_scope", "message": f"{prefix} is missing read_scope"})
        if not text:
            errors.append({"code": "empty_packet_text", "message": f"{prefix} text is empty"})
        elif clean_text(packet.get("text_sha256")) != sha256_text(text):
            errors.append({"code": "text_hash_mismatch", "message": f"{prefix} text_sha256 does not match text"})
        source_path = resolve_source_path(packet.get("source_path"))
        if source_kind in {"pdf", "local-note"} and source_path is None:
            errors.append({"code": "missing_source_path", "message": f"{prefix} source_path is unavailable"})
        if source_path is not None and clean_text(packet.get("source_sha256")) != sha256_file(source_path):
            errors.append({"code": "source_hash_mismatch", "message": f"{prefix} source_sha256 does not match source"})
        if source_kind == "pdf":
            document_pages = packet.get("document_pages")
            pages_read = packet.get("pages_read")
            if not isinstance(document_pages, int) or document_pages < 1 or pages_read != document_pages:
                errors.append({"code": "incomplete_fulltext", "message": f"{prefix} did not read every PDF page"})
            expected_scope = f"pages:1-{document_pages}" if isinstance(document_pages, int) else ""
            if read_scope != expected_scope:
                errors.append({"code": "invalid_fulltext_scope", "message": f"{prefix} read_scope must be {expected_scope}"})
    return candidates_by_id, ranking_by_id, packets_by_id, errors


def year_value(candidate: dict[str, Any]) -> int | str | None:
    raw = candidate.get("year") or candidate.get("publicationYear")
    if raw not in (None, ""):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    for key in ("publicationDate", "published", "updated"):
        match = re.match(r"(\d{4})", clean_text(candidate.get(key)))
        if match:
            return int(match.group(1))
    return None


def paper_node(packet: dict[str, Any], candidate: dict[str, Any], ranking: dict[str, Any]) -> dict[str, Any]:
    paper_id = clean_text(packet.get("id"))
    citation_count = ranking.get("citationCount")
    if citation_count is None:
        citation_count = research_candidate_rank.citation_count(candidate)
    upvotes = ranking.get("hf_upvotes")
    if upvotes is None:
        upvotes = research_candidate_rank.hf_upvotes(candidate)
    return {
        "node_id": f"paper:{paper_id}",
        "kind": "paper",
        "paper_id": paper_id,
        "title": clean_text(candidate.get("title")) or clean_text(packet.get("title")),
        "authors": as_list(candidate.get("authors")) or as_list(packet.get("authors")),
        "year": year_value(candidate),
        "venue": clean_text(candidate.get("venue")) or None,
        "evidence_level": clean_text(packet.get("evidence_level")),
        "read_scope": clean_text(packet.get("read_scope")),
        "source_kind": clean_text(packet.get("source_kind")),
        "relevance": candidate.get("relevance"),
        "rank": ranking.get("rank") or packet.get("rank"),
        "citationCount": citation_count,
        "hf_upvotes": upvotes,
        "local_note_path": clean_text(candidate.get("local_note_path")) or None,
        "local_pdf_path": clean_text(packet.get("pdf_path")) or None,
        "arxiv_id": clean_text(candidate.get("arxiv_id")) or None,
        "doi": clean_text(candidate.get("doi")) or None,
        "url": clean_text(candidate.get("url")) or None,
    }


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "paper"


def extraction_scaffold(packet: dict[str, Any], candidate: dict[str, Any]) -> str:
    paper_id = clean_text(packet.get("id"))
    title = clean_text(candidate.get("title")) or clean_text(packet.get("title")) or paper_id
    return (
        f"# {title}\n\n"
        f"- **Paper ID**: {paper_id}\n"
        f"- **Source kind**: {clean_text(packet.get('source_kind'))}\n"
        f"- **Evidence level**: {clean_text(packet.get('evidence_level'))}\n"
        f"- **Read scope**: {clean_text(packet.get('read_scope'))}\n"
        f"- **Packet SHA256**: {clean_text(packet.get('text_sha256'))}\n"
        "- **Extraction status**: pending\n\n"
        "## Evidence\n\n"
        "## Claims\n\n"
        "## Problems\n\n"
        "## Heuristics\n"
    )


def ensure_markdown(path: Path, content: str) -> bool:
    if path.exists():
        return False
    atomic_write_text(path, content)
    return True


def file_descriptor(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def optional_file_descriptor(path: Path) -> dict[str, str | None]:
    return {"path": str(path), "sha256": sha256_file(path) if path.exists() else None}


def stage_after_input_change(previous: Any, *, changed: bool, files_exist: bool) -> dict[str, Any]:
    if not changed and isinstance(previous, dict):
        return previous
    return {"status": "stale" if files_exist else "pending", "updated_at": utc_now()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing synthesis/packets/index.json.")
    parser.add_argument("--metadata", type=Path, help="Default: <survey-root>/search/candidate_metadata.json")
    parser.add_argument("--ranking", type=Path, help="Default: <survey-root>/search/candidate_ranking.json")
    parser.add_argument("--packet-index", type=Path, help="Default: <survey-root>/synthesis/packets/index.json")
    parser.add_argument("--manifest", type=Path, help="Default: <survey-root>/synthesis/manifest.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    synthesis_dir = survey_root / "synthesis"
    packets_dir = synthesis_dir / "packets"
    extraction_dir = synthesis_dir / "extraction"
    logic_dir = synthesis_dir / "logic"
    evidence_dir = synthesis_dir / "evidence"
    validation_dir = synthesis_dir / "validation"
    metadata_path = args.metadata or (survey_root / "search" / "candidate_metadata.json")
    ranking_path = args.ranking or (survey_root / "search" / "candidate_ranking.json")
    packet_index_path = args.packet_index or (packets_dir / "index.json")
    manifest_path = args.manifest or (synthesis_dir / "manifest.json")
    candidates, rankings, packets, errors = validate_inputs(metadata_path, ranking_path, packet_index_path)
    report = {
        "valid": not errors,
        "stage": "packets",
        "candidate_metadata": str(metadata_path),
        "candidate_ranking": str(ranking_path),
        "packet_index": str(packet_index_path),
        "packet_count": len(packets),
        "errors": errors,
    }
    write_json(validation_dir / "packets.json", report)
    if errors:
        now = utc_now()
        invalid_manifest = {
            "schema_version": SCHEMA_VERSION,
            "survey_root": str(survey_root),
            "generated_at": now,
            "pipeline_input_sha256": None,
            "selected_paper_ids": list(packets),
            "inputs": {
                "candidate_metadata": optional_file_descriptor(metadata_path),
                "candidate_ranking": optional_file_descriptor(ranking_path),
                "packet_index": optional_file_descriptor(packet_index_path),
            },
            "outputs": {
                "packets": str(packet_index_path),
                "extraction_files": [],
                "candidate_index": str(extraction_dir / "candidate_index.jsonl"),
                "clusters": str(extraction_dir / "clusters.md"),
                "logic_markdown": str(logic_dir / "logic.md"),
                "relations_markdown": str(logic_dir / "relations.md"),
                "evidence_markdown": str(evidence_dir / "evidence.md"),
                "papers": str(evidence_dir / "papers.jsonl"),
                "evidence": str(evidence_dir / "evidence.jsonl"),
                "claims": str(logic_dir / "claims.jsonl"),
                "problems": str(logic_dir / "problems.jsonl"),
                "heuristics": str(logic_dir / "heuristics.jsonl"),
                "relations": str(logic_dir / "relations.jsonl"),
                "literature_review": str(survey_root / "notes" / "literature_review.md"),
                "query_pack": str(synthesis_dir / "query_pack.md"),
            },
            "stages": {
                "packets": {"status": "invalid", "updated_at": now, "report": str(validation_dir / "packets.json")},
                "extraction": {"status": "pending", "updated_at": now},
                "evidence": {"status": "pending", "updated_at": now},
                "canonical": {"status": "pending", "updated_at": now},
                "projections": {"status": "pending", "updated_at": now},
                "relations": {"status": "pending", "updated_at": now},
                "review": {"status": "pending", "updated_at": now},
                "query_pack": {"status": "pending", "updated_at": now},
            },
        }
        write_json(manifest_path, invalid_manifest)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    input_descriptors = {
        "candidate_metadata": file_descriptor(metadata_path),
        "candidate_ranking": file_descriptor(ranking_path),
        "packet_index": file_descriptor(packet_index_path),
    }
    input_signature = sha256_text("|".join(item["sha256"] for item in input_descriptors.values()))
    previous = load_json(manifest_path) if manifest_path.exists() else {}
    changed = previous.get("pipeline_input_sha256") != input_signature
    previous_stages = previous.get("stages") if isinstance(previous.get("stages"), dict) else {}

    expected_extraction_paths = [extraction_dir / f"{safe_filename(paper_id)}.md" for paper_id in packets]
    preexisting = {
        "extraction": any(path.exists() for path in expected_extraction_paths),
        "evidence": (evidence_dir / "evidence.md").exists(),
        "canonical": (extraction_dir / "clusters.md").exists() or (logic_dir / "logic.md").exists(),
        "projections": any((logic_dir / name).exists() for name in ("claims.jsonl", "problems.jsonl", "heuristics.jsonl")),
        "relations": (logic_dir / "relations.md").exists() or (logic_dir / "relations.jsonl").exists(),
        "review": (survey_root / "notes" / "literature_review.md").exists(),
        "query_pack": (synthesis_dir / "query_pack.md").exists(),
    }
    created_scaffolds: list[str] = []
    created_extraction = False
    extraction_paths: list[Path] = []
    paper_nodes: list[dict[str, Any]] = []
    for paper_id, packet in packets.items():
        candidate = candidates[paper_id]
        paper_nodes.append(paper_node(packet, candidate, rankings[paper_id]))
        extraction_path = extraction_dir / f"{safe_filename(paper_id)}.md"
        extraction_paths.append(extraction_path)
        if ensure_markdown(extraction_path, extraction_scaffold(packet, candidate)):
            created_scaffolds.append(str(extraction_path))
            created_extraction = True

    clusters_path = extraction_dir / "clusters.md"
    logic_path = logic_dir / "logic.md"
    relations_markdown_path = logic_dir / "relations.md"
    created_stage_scaffolds: set[str] = set()
    for stage, path, content in (
        (
            "canonical",
            clusters_path,
            "# Canonical Decisions\n\n## Claims\n\n## Problems\n\n## Heuristics\n\n"
            "## Excluded Candidates\n",
        ),
        ("canonical", logic_path, "# Literature Logic\n\n## Claims\n\n## Problems\n\n## Heuristics\n"),
        ("relations", relations_markdown_path, "# Literature Relations\n\n## Relations\n"),
    ):
        if ensure_markdown(path, content):
            created_scaffolds.append(str(path))
            created_stage_scaffolds.add(stage)

    papers_path = evidence_dir / "papers.jsonl"
    write_jsonl(papers_path, paper_nodes)
    current_ids = {path.stem for path in extraction_paths}
    obsolete = [str(path) for path in sorted(extraction_dir.glob("*.md")) if path.name != "clusters.md" and path.stem not in current_ids]

    generated_at = utc_now()
    packets_stage = {
        "status": "valid",
        "updated_at": generated_at,
        "input_sha256": input_signature,
        "output_hashes": {"papers": sha256_file(papers_path)},
        "report": str(validation_dir / "packets.json"),
    }
    stages = {"packets": packets_stage}
    for stage in ("extraction", "evidence", "canonical", "projections", "relations", "review", "query_pack"):
        stages[stage] = stage_after_input_change(
            previous_stages.get(stage),
            changed=changed,
            files_exist=preexisting[stage],
        )
    if created_extraction:
        stages["extraction"] = {"status": "pending", "updated_at": generated_at}
        stages["evidence"] = {"status": "pending", "updated_at": generated_at}
        stages["canonical"] = {"status": "pending", "updated_at": generated_at}
        stages["projections"] = {"status": "pending", "updated_at": generated_at}
        stages["relations"] = {"status": "pending", "updated_at": generated_at}
        stages["review"] = {"status": "pending", "updated_at": generated_at}
        stages["query_pack"] = {"status": "pending", "updated_at": generated_at}
    elif "canonical" in created_stage_scaffolds:
        stages["canonical"] = {"status": "pending", "updated_at": generated_at}
        stages["projections"] = {"status": "pending", "updated_at": generated_at}
        stages["relations"] = {"status": "pending", "updated_at": generated_at}
        stages["review"] = {"status": "pending", "updated_at": generated_at}
        stages["query_pack"] = {"status": "pending", "updated_at": generated_at}
    elif "relations" in created_stage_scaffolds:
        stages["relations"] = {"status": "pending", "updated_at": generated_at}
        stages["review"] = {"status": "pending", "updated_at": generated_at}
        stages["query_pack"] = {"status": "pending", "updated_at": generated_at}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "survey_root": str(survey_root),
        "generated_at": generated_at,
        "pipeline_input_sha256": input_signature,
        "selected_paper_ids": list(packets),
        "inputs": input_descriptors,
        "outputs": {
            "packets": str(packet_index_path),
            "extraction_files": [str(path) for path in extraction_paths],
            "candidate_index": str(extraction_dir / "candidate_index.jsonl"),
            "clusters": str(clusters_path),
            "logic_markdown": str(logic_path),
            "relations_markdown": str(relations_markdown_path),
            "evidence_markdown": str(evidence_dir / "evidence.md"),
            "papers": str(papers_path),
            "evidence": str(evidence_dir / "evidence.jsonl"),
            "claims": str(logic_dir / "claims.jsonl"),
            "problems": str(logic_dir / "problems.jsonl"),
            "heuristics": str(logic_dir / "heuristics.jsonl"),
            "relations": str(logic_dir / "relations.jsonl"),
            "literature_review": str(survey_root / "notes" / "literature_review.md"),
            "query_pack": str(synthesis_dir / "query_pack.md"),
        },
        "stages": stages,
        "paper_count": len(paper_nodes),
        "created_scaffolds": created_scaffolds,
        "obsolete_extraction_files": obsolete,
        "node_id_policy": {
            "paper": "paper:<candidate-id>",
            "extraction_evidence": "evidence:<candidate-id>-<short-slug>",
            "extraction_claim": "claim:<candidate-id>-<short-slug>",
            "extraction_problem": "problem:<candidate-id>-<short-slug>",
            "extraction_heuristic": "heuristic:<candidate-id>-<short-slug>",
            "canonical_evidence": "evidence:<candidate-id>-<short-slug>",
            "canonical_claim": "claim:<short-slug>",
            "canonical_problem": "problem:<short-slug>",
            "canonical_heuristic": "heuristic:<short-slug>",
        },
    }
    write_json(manifest_path, manifest)
    print(json.dumps({**manifest, "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
