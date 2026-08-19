#!/usr/bin/env python3
"""Build a deterministic survey query pack from research-lit synthesis artifacts."""

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

from research_artifact_io import atomic_write_text, sha256_file, sha256_text, write_json  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402
import research_synthesis_compile  # noqa: E402
import research_note_tasks  # noqa: E402


SECTION_BUDGETS = {
    "scope": 700,
    "papers": 1800,
    "claims": 1200,
    "heuristics": 1000,
    "relationships": 1300,
    "problems": 1300,
    "wiki_notes": 600,
    "literature_review": 900,
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def load_optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    return load_json(path)


def load_required_wiki_notes(survey_root: Path) -> list[dict[str, Any]]:
    return research_note_tasks.load_validated_wiki_notes(survey_root)


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[:max_chars]
    chunk = text[: max_chars - 16]
    newline = chunk.rfind("\n")
    if newline > max_chars // 2:
        chunk = chunk[:newline]
    return chunk.rstrip() + "\n...(truncated)"


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def node_label(node: dict[str, Any]) -> str:
    return first_nonempty(node.get("title"), node.get("label"), node.get("node_id"))


def format_queries(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [clean_text(item) for item in payload if clean_text(item)]
    if not isinstance(payload, dict):
        return []
    for key in ("queries", "search_queries", "SEARCH_QUERIES"):
        value = payload.get(key)
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = first_nonempty(item.get("query"), item.get("q"), item.get("text"))
                else:
                    text = clean_text(item)
                if text:
                    result.append(text)
            return result
    topic = clean_text(payload.get("topic") or payload.get("topic_name"))
    return [topic] if topic else []


def numeric_rank(node: dict[str, Any]) -> tuple[int, str]:
    value = node.get("rank")
    try:
        return int(value), clean_text(node.get("node_id"))
    except (TypeError, ValueError):
        return 10**9, clean_text(node.get("node_id"))


def build_scope_section(survey_root: Path, queries_payload: Any) -> str:
    lines = ["## Scope", f"- Survey root: `{survey_root}`"]
    queries = format_queries(queries_payload)
    if queries:
        lines.append("- Queries: " + "; ".join(queries))
    return "\n".join(lines) + "\n"


def evidence_paper_id(record: dict[str, Any]) -> str:
    match = re.search(r"(?:^|;\s*)paper:([^;\s]+)", clean_text(record.get("provenance")))
    return match.group(1) if match else ""


def build_papers_section(papers: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    evidence_by_paper: dict[str, list[str]] = {}
    for record in evidence:
        paper_id = evidence_paper_id(record)
        evidence_text = clean_text(record.get("finding"))
        if paper_id and evidence_text:
            evidence_by_paper.setdefault(paper_id, []).append(evidence_text)

    lines = [f"## Key Papers ({len(papers)} total)"]
    for paper in sorted(papers, key=numeric_rank)[:12]:
        node_id = clean_text(paper.get("node_id"))
        paper_id = clean_text(paper.get("paper_id")) or node_id.removeprefix("paper:")
        title = node_label(paper)
        evidence_level = clean_text(paper.get("evidence_level"))
        paper_evidence = evidence_by_paper.get(paper_id, [])
        parts = [f"- [{node_id}] {title}"]
        details: list[str] = []
        if paper_evidence:
            details.append(f"evidence: {paper_evidence[0]}")
        if evidence_level:
            details.append(f"scope: {evidence_level}")
        if details:
            parts.append(" - " + "; ".join(details))
        lines.append("".join(parts))
    return "\n".join(lines) + "\n"


def build_claims_section(claims: list[dict[str, Any]]) -> str:
    lines = [f"## Claims ({len(claims)} total)"]
    for claim in claims[:8]:
        node_id = clean_text(claim.get("node_id"))
        title = clean_text(claim.get("title"))
        statement = clean_text(claim.get("statement"))
        status = clean_text(claim.get("status"))
        evidence_nodes = claim.get("evidence_nodes")
        evidence_text = ""
        if isinstance(evidence_nodes, list) and evidence_nodes:
            evidence_text = " evidence: " + ", ".join(clean_text(item) for item in evidence_nodes[:5] if clean_text(item))
        suffix = f" ({status})" if status else ""
        body = statement if statement and statement != title else ""
        lines.append(f"- [{node_id}] {title}{suffix}" + (f": {body}" if body else "") + evidence_text)
    return "\n".join(lines) + "\n"


def build_relationships_section(relations: list[dict[str, Any]]) -> str:
    lines = [f"## Relationships ({len(relations)} total)"]
    for relation in relations[:20]:
        source = clean_text(relation.get("from"))
        relation_type = clean_text(relation.get("type"))
        target = clean_text(relation.get("to"))
        confidence = clean_text(relation.get("confidence"))
        evidence_level = clean_text(relation.get("evidence_level"))
        read_scope = clean_text(relation.get("read_scope"))
        evidence = clean_text(relation.get("evidence"))
        source_packets = relation.get("source_packets")
        packets = ""
        if isinstance(source_packets, list) and source_packets:
            packets = " packets: " + ", ".join(clean_text(item) for item in source_packets[:5] if clean_text(item))
        provenance = ", ".join(item for item in (confidence, evidence_level, read_scope) if item)
        suffix = f" ({provenance})" if provenance else ""
        lines.append(f"- {source} --{relation_type}--> {target}{suffix}: {evidence}{packets}")
    return "\n".join(lines) + "\n"


def build_problems_section(problems: list[dict[str, Any]]) -> str:
    lines = [f"## Open Problems ({len(problems)} total)"]
    for problem in problems[:8]:
        node_id = clean_text(problem.get("node_id"))
        label = clean_text(problem.get("title"))
        status = clean_text(problem.get("status"))
        gap = clean_text(problem.get("gap"))
        importance = clean_text(problem.get("importance"))
        title = f"- [{node_id}] {label}" + (f" ({status})" if status else "")
        body = gap
        if importance:
            body = (body + " " if body else "") + f"Importance: {importance}"
        lines.append(title + (f": {body}" if body and body != label else ""))
    return "\n".join(lines) + "\n"


def build_heuristics_section(heuristics: list[dict[str, Any]]) -> str:
    lines = [f"## Heuristics ({len(heuristics)} total)"]
    for heuristic in heuristics[:8]:
        node_id = clean_text(heuristic.get("node_id"))
        title = clean_text(heuristic.get("title"))
        prescription = clean_text(heuristic.get("prescription"))
        status = clean_text(heuristic.get("status"))
        suffix = f" ({status})" if status else ""
        lines.append(f"- [{node_id}] {title}{suffix}" + (f": {prescription}" if prescription else ""))
    return "\n".join(lines) + "\n"


def build_wiki_notes_section(wiki_notes_payload: Any) -> str:
    notes = wiki_notes_payload if isinstance(wiki_notes_payload, list) else []
    lines = [f"## Wiki Notes ({len(notes)} total)"]
    for note in notes[:12]:
        if not isinstance(note, dict):
            continue
        note_id = clean_text(note.get("id"))
        title = first_nonempty(note.get("title"), note_id)
        path = clean_text(note.get("note_path"))
        note_type = clean_text(note.get("note_type"))
        suffix = f" [{note_type}]" if note_type else ""
        lines.append(f"- [{note_id}] {title}{suffix}: `{path}`")
    return "\n".join(lines) + "\n"


def build_literature_review_section(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
    if not text:
        return ""
    return "## Literature Review Snapshot\n" + clean_text(text) + "\n"


def budget_section(section: str, key: str) -> str:
    return truncate_text(section, SECTION_BUDGETS[key]).rstrip() + "\n"


def assemble_pack(sections: list[str], max_chars: int) -> str:
    pack = "# Survey Query Pack\n\n_Auto-generated from survey synthesis artifacts. Do not hand-edit._\n\n"
    for section in sections:
        if len(pack) + len(section) <= max_chars:
            pack += section + "\n"
            continue
        remaining = max_chars - len(pack) - 16
        if remaining > 100:
            pack += truncate_text(section, remaining).rstrip() + "\n"
        break
    return pack.rstrip() + "\n"


def build_pack(survey_root: Path, *, max_chars: int = 8000) -> str:
    synthesis_dir = survey_root / "synthesis"
    logic_dir = synthesis_dir / "logic"
    evidence_dir = synthesis_dir / "evidence"
    manifest = load_optional_json(synthesis_dir / "manifest.json")
    stages = manifest.get("stages") if isinstance(manifest, dict) else {}
    for stage in ("projections", "relations", "review"):
        if not isinstance(stages, dict) or stages.get(stage, {}).get("status") != "valid":
            raise ValueError(f"synthesis stage is not valid: {stage}")
    review_path = survey_root / "notes" / "literature_review.md"
    if not review_path.exists():
        raise FileNotFoundError(f"required literature review not found: {review_path}")
    queries_payload = load_optional_json(survey_root / "search" / "queries.json")
    papers = load_jsonl(evidence_dir / "papers.jsonl")
    evidence = load_jsonl(evidence_dir / "evidence.jsonl")
    claims = load_jsonl(logic_dir / "claims.jsonl")
    problems = load_jsonl(logic_dir / "problems.jsonl")
    heuristics = load_jsonl(logic_dir / "heuristics.jsonl")
    relations = load_jsonl(logic_dir / "relations.jsonl")
    wiki_notes_payload = load_required_wiki_notes(survey_root)

    sections = [
        budget_section(build_scope_section(survey_root, queries_payload), "scope"),
        budget_section(build_papers_section(papers, evidence), "papers"),
        budget_section(build_claims_section(claims), "claims"),
        budget_section(build_relationships_section(relations), "relationships"),
        budget_section(build_problems_section(problems), "problems"),
        budget_section(build_heuristics_section(heuristics), "heuristics"),
        budget_section(build_wiki_notes_section(wiki_notes_payload), "wiki_notes"),
    ]
    review_section = build_literature_review_section(review_path)
    if review_section:
        sections.append(budget_section(review_section, "literature_review"))
    return assemble_pack(sections, max_chars)


def query_pack_input_signature(survey_root: Path) -> str:
    synthesis_dir = survey_root / "synthesis"
    paths = [
        survey_root / "search" / "queries.json",
        synthesis_dir / "evidence" / "papers.jsonl",
        synthesis_dir / "evidence" / "evidence.jsonl",
        synthesis_dir / "logic" / "claims.jsonl",
        synthesis_dir / "logic" / "problems.jsonl",
        synthesis_dir / "logic" / "heuristics.jsonl",
        synthesis_dir / "logic" / "relations.jsonl",
        survey_root / "notes" / "literature_review.md",
        synthesis_dir / "wiki_notes.json",
        synthesis_dir / "validation" / "paper_notes.json",
    ]
    required = paths[1:]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"query-pack input missing: {missing[0]}")
    return sha256_text("|".join(f"{path}:{sha256_file(path)}" for path in paths if path.exists()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing synthesis nodes and relations.")
    parser.add_argument("--output", type=Path, help="Default: <survey-root>/synthesis/query_pack.md")
    parser.add_argument("--max-chars", type=int, default=8000)
    parser.add_argument("--check", action="store_true", help="Exit non-zero if existing query_pack.md differs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_chars < 500:
        print("Error: --max-chars must be >= 500", file=sys.stderr)
        return 2
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
        manifest = load_json(survey_root / "synthesis" / "manifest.json")
        current_errors = research_synthesis_compile.audit_review_current(survey_root, manifest)
        if current_errors:
            raise ValueError(f"synthesis artifacts are stale: {current_errors[0]['message']}")
        pack = build_pack(survey_root, max_chars=args.max_chars)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    output_path = args.output or (survey_root / "synthesis" / "query_pack.md")
    if args.check:
        existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        query_stage = manifest.get("stages", {}).get("query_pack", {})
        current_input = query_pack_input_signature(survey_root)
        current_output = sha256_text(existing)
        if (
            existing != pack
            or query_stage.get("status") != "valid"
            or query_stage.get("input_sha256") != current_input
            or query_stage.get("output_sha256") != current_output
        ):
            print(json.dumps({"ok": False, "reason": "query_pack_out_of_date", "output": str(output_path)}, indent=2))
            return 1
        print(json.dumps({"ok": True, "output": str(output_path), "chars": len(pack)}, indent=2))
        return 0

    atomic_write_text(output_path, pack)
    manifest.setdefault("stages", {})["query_pack"] = {
        "status": "valid",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": query_pack_input_signature(survey_root),
        "output_sha256": sha256_file(output_path),
    }
    write_json(survey_root / "synthesis" / "manifest.json", manifest)
    print(json.dumps({"output": str(output_path), "chars": len(pack), "max_chars": args.max_chars}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
