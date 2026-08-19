#!/usr/bin/env python3
"""Compatibility orchestration for the former score/reference research workflow.

The current three-stage ``research-lit`` controller uses the
``research_candidate_*`` and ``research_synthesis_*`` tools instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .validate_paper_artifacts import validate_paper_folder
else:
    from validate_paper_artifacts import validate_paper_folder

ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})(?:v\d+)?$")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def paper_id(record: dict[str, Any]) -> str:
    value = record.get("paper_id") or record.get("id")
    if not value:
        raise ValueError("paper record is missing paper_id/id")
    return str(value)


def existing_notes(topic_folder: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    result = {}
    for path in sorted(topic_folder.glob("*/note.json")):
        try:
            note = read_json(path)
            result[str(note["paper_id"])] = (note, path.parent)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return result


def build_reference_pool(args: argparse.Namespace) -> int:
    candidates = read_json(args.candidates, [])
    old = existing_notes(args.topic_folder)
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pid, (note, folder) in old.items():
        pool.append({
            "id": pid,
            "title": note.get("title"),
            "paper_folder": str(folder),
            "references": [],
            "reference_fetch_status": "missing",
            "reference_fetch_attempts": 0,
            "record_origin": "existing",
        })
        seen.add(pid)
    new_count = 0
    for candidate in candidates:
        pid = paper_id(candidate)
        if pid in seen:
            continue
        record = dict(candidate)
        record["id"] = pid
        record.setdefault("references", [])
        record.setdefault("reference_fetch_status", "missing")
        record.setdefault("reference_fetch_attempts", 0)
        matches = sorted(args.topic_folder.glob(f"{pid}*"))
        if matches:
            record["paper_folder"] = str(matches[0])
        record["record_origin"] = "new"
        pool.append(record)
        seen.add(pid)
        new_count += 1
    write_json(args.output, pool)
    print(json.dumps({"existing": len(old), "new": new_count, "total": len(pool)}))
    return 0


def _mapping(payload: Any, key: str = "paper_id") -> dict[str, Any]:
    if isinstance(payload, dict):
        return {str(k): v for k, v in payload.items()}
    return {str(item.get(key) or item.get("id")): item for item in (payload or [])}


def filter_relevant(args: argparse.Namespace) -> int:
    candidates = {paper_id(item): item for item in read_json(args.candidates, [])}
    verification_payload = read_json(args.verification, {})
    verified_items = verification_payload.get(
        "papers", verification_payload if isinstance(verification_payload, list) else []
    )
    verified = {paper_id(item) for item in verified_items if item.get("status") == "verified"}
    judgments = _mapping(read_json(args.judgments, []))
    unexpected = sorted(set(judgments) - verified)
    if unexpected:
        raise ValueError(f"relevance judgments include non-verified IDs: {unexpected}")
    missing = sorted(verified - set(judgments))
    if missing:
        raise ValueError(f"verified papers lack binary relevance judgments: {missing}")

    output = []
    zero_count = 0
    for pid in sorted(verified):
        judgment = judgments[pid]
        value = judgment.get("relevance") if isinstance(judgment, dict) else judgment
        if value not in (0, 1) or isinstance(value, bool):
            raise ValueError(f"paper {pid} relevance must be integer 0 or 1")
        if value == 0:
            zero_count += 1
            continue
        if pid not in candidates:
            raise ValueError(f"verified paper {pid} is missing from candidate metadata")
        record = dict(candidates[pid])
        record["relevance"] = 1
        output.append(record)
    write_json(args.output, output)
    print(json.dumps({"verified": len(verified), "relevant": len(output), "excluded": zero_count}))
    return 0


def build_score_manifest(args: argparse.Namespace) -> int:
    pool = {paper_id(item): item for item in read_json(args.reference_pool, [])}
    candidates = {paper_id(item): item for item in read_json(args.candidates, [])}
    verification_payload = read_json(args.verification, {})
    verified = {
        paper_id(item) for item in verification_payload.get("papers", verification_payload if isinstance(verification_payload, list) else [])
        if item.get("status") == "verified"
    }
    judgments = _mapping(read_json(args.judgments, {}))
    hf = _mapping(read_json(args.hf_results, {})) if args.hf_results else {}
    old = existing_notes(args.topic_folder)
    manifest: list[dict[str, Any]] = []
    for pid in sorted(set(old) | (set(candidates) & verified)):
        refs = pool.get(pid, {}).get("references", [])
        if pid in old:
            note, folder = old[pid]
            score = note.get("relevance_score") or {}
            topical, novelty = score.get("topical_relevance"), score.get("novelty")
            if topical is None or novelty is None:
                raise ValueError(f"existing paper {pid} lacks reusable topical_relevance/novelty")
            venue = note.get("venue") or ("arXiv preprint" if ARXIV_ID.fullmatch(pid) else None)
            source = note.get("discovery_source")
            published = note.get("published_date")
            folder_value = str(folder)
            origin = "existing"
        else:
            candidate = candidates[pid]
            judgment = judgments.get(pid)
            if not judgment:
                raise ValueError(f"new verified paper {pid} lacks a judgment")
            if isinstance(judgment, (list, tuple)):
                topical, novelty = judgment
            else:
                topical, novelty = judgment["topical_relevance"], judgment["novelty"]
            venue = candidate.get("venue") or ("arXiv preprint" if ARXIV_ID.fullmatch(pid) else None)
            source = candidate.get("source") or candidate.get("discovery_source")
            published = candidate.get("published_date")
            folder_value = None
            origin = "new"
        hf_value = hf.get(pid)
        if isinstance(hf_value, dict):
            hf_value = hf_value.get("submitted_on_daily_at") or hf_value.get("hf_submitted_on_daily_at")
        manifest.append({
            "paper_id": pid, "paper_folder": folder_value, "published_date": published,
            "venue": venue, "discovery_source": source,
            "topical_relevance": topical, "novelty": novelty,
            "hf_submitted_on_daily_at": hf_value, "references": refs, "record_origin": origin,
        })
    write_json(args.output, manifest)
    print(json.dumps({"existing": len(old), "new_verified": len(manifest) - len(old), "total": len(manifest)}))
    return 0


def slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return value[:90].rstrip("-") or "paper"


def select(args: argparse.Namespace) -> int:
    scores = read_json(args.scores, [])
    candidates = {paper_id(item): item for item in read_json(args.candidates, [])}
    verification_payload = read_json(args.verification, {})
    verification = {paper_id(item): item for item in verification_payload.get("papers", verification_payload if isinstance(verification_payload, list) else [])}
    selected = []
    exclusions = []
    for score in sorted(scores, key=lambda item: item["relevance_score"]["total"], reverse=True):
        pid = paper_id(score)
        candidate, verdict = candidates.get(pid), verification.get(pid)
        if not candidate or not verdict or verdict.get("status") != "verified":
            continue
        if not ARXIV_ID.fullmatch(pid):
            exclusions.append({
                "paper_id": pid,
                "reason": "no_supported_pdf_route",
                "relevance_score": score["relevance_score"],
            })
            continue
        if len(selected) >= args.max_papers:
            exclusions.append({
                "paper_id": pid,
                "reason": "analysis_cap_reached",
                "relevance_score": score["relevance_score"],
            })
            continue
        matches = sorted(args.topic_folder.glob(f"{pid}*"))
        folder = matches[0] if matches else args.topic_folder / f"{pid}-{slugify(candidate.get('title') or pid)}"
        pdf = folder / f"{pid}.pdf"
        selected.append({
            "paper_id": pid,
            "paper_folder": str(folder),
            "pdf_path": str(pdf) if pdf.exists() else None,
            "verification_status": verdict.get("status"),
            "verification_method": verdict.get("method"),
            "relevance_score": score["relevance_score"],
        })
    write_json(args.output, selected)
    if args.exclusions_output:
        write_json(args.exclusions_output, exclusions)
    print(json.dumps({"selected": len(selected), "excluded": len(exclusions), "output": str(args.output)}))
    return 0


def refresh_selected_pdfs(args: argparse.Namespace) -> int:
    selected = read_json(args.selected, [])
    missing = []
    ready = 0
    for item in selected:
        pid = paper_id(item)
        folder = Path(item["paper_folder"])
        preferred = folder / f"{pid}.pdf"
        candidates = [preferred] if preferred.is_file() else sorted(folder.glob("*.pdf"))
        valid = [path for path in candidates if path.is_file() and path.stat().st_size > args.min_bytes]
        if len(valid) == 1:
            item["pdf_path"] = str(valid[0])
            ready += 1
        else:
            item["pdf_path"] = None
            missing.append({
                "paper_id": pid,
                "reason": "no_valid_pdf" if not valid else "multiple_valid_pdfs",
            })
    write_json(args.output, selected)
    print(json.dumps({"ready": ready, "missing": missing, "output": str(args.output)}))
    return 1 if missing else 0


def finalize_scores(args: argparse.Namespace) -> int:
    scores = read_json(args.scores, [])
    selected = {paper_id(item): item for item in read_json(args.selected, [])}
    for item in scores:
        chosen = selected.get(paper_id(item))
        if chosen:
            if chosen.get("relevance_score") != item.get("relevance_score"):
                raise ValueError(f"selected score changed for {paper_id(item)}")
            item["paper_folder"] = chosen["paper_folder"]
    write_json(args.output, scores)
    if args.patch_notes:
        command = [sys.executable, str(Path(__file__).with_name("relevance_score.py")), "patch", "--scores", str(args.output)]
        return subprocess.run(command).returncode
    print(json.dumps({"total": len(scores), "folders_filled": len(selected)}))
    return 0


def tags_for(summary: str, paper_type: str) -> str:
    tags = [paper_type or "paper-analysis"]
    for needle, tag in (("reasoning", "reasoning"), ("reinforcement", "reinforcement-learning"), ("budget", "budget-aware"), ("compression", "compression"), ("early exit", "early-exit"), ("latent", "latent-reasoning"), ("token", "token-efficiency")):
        if needle in summary.casefold() and tag not in tags:
            tags.append(tag)
        if len(tags) == 4:
            break
    if len(tags) < 2:
        tags.append("research-paper")
    return ",".join(tags[:4])


def run_step8(args: argparse.Namespace) -> int:
    ranked = sorted(read_json(args.scores, []), key=lambda item: item["relevance_score"]["total"], reverse=True)
    ranked = [item for item in ranked if item.get("paper_folder") and (Path(item["paper_folder"]) / "note.json").is_file() and (Path(item["paper_folder"]) / "logic.json").is_file()][: args.limit]
    results = []
    for item in ranked:
        folder = Path(item["paper_folder"])
        note, logic = read_json(folder / "note.json"), read_json(folder / "logic.json")
        insights = logic.get("methodology", {}).get("key_insights", []) or logic.get("challenges", {}).get("key_insights", [])
        command = [sys.executable, str(Path(__file__).with_name("research_wiki.py")), "ingest_paper", str(args.wiki_root)]
        pid = paper_id(item)
        if ARXIV_ID.fullmatch(pid):
            command.extend(["--arxiv-id", pid])
        else:
            year = str(note.get("published_date") or "")[:4]
            command.extend(["--title", note.get("title", ""), "--authors", ", ".join(note.get("authors", [])), "--year", year or "0", "--venue", note.get("venue") or "unknown"])
            if pid.lower().startswith("10."):
                command.extend(["--external-id-doi", pid])
        command.extend(["--tags", tags_for(logic.get("methodology", {}).get("summary", ""), note.get("paper_type", ""))])
        if insights:
            command.extend(["--thesis", insights[0]["text"]])
        result = subprocess.run(command, text=True, capture_output=True)
        results.append({"paper_id": paper_id(item), "score": item["relevance_score"]["total"], "returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
    write_json(args.output, results)
    failed = sum(item["returncode"] != 0 for item in results)
    print(json.dumps({"processed": len(results), "failed": failed}))
    return 1 if failed else 0


def audit_run(args: argparse.Namespace) -> int:
    issues: list[str] = []
    status = read_json(args.reference_status, {}) if args.reference_status else {}
    summary = status.get("summary", {}) if isinstance(status, dict) else {}
    if summary and (summary.get("coverage", 0) < args.coverage_threshold or not summary.get("ready_for_scoring")):
        issues.append("reference_coverage_below_threshold")
    selected = read_json(args.selected, []) if args.selected else []
    for item in selected:
        folder = Path(item["paper_folder"])
        if not folder.exists():
            issues.append(f"selected_folder_missing:{paper_id(item)}")
            continue
        issues.extend(f"{paper_id(item)}:{issue}" for issue in validate_paper_folder(folder))
    if args.scores and args.final_scores:
        before = {paper_id(item): item.get("relevance_score") for item in read_json(args.scores, [])}
        after = {paper_id(item): item.get("relevance_score") for item in read_json(args.final_scores, [])}
        if before != after:
            issues.append("step5_scores_changed_during_finalization")
    if args.source_status:
        sources = read_json(args.source_status, [])
        if not isinstance(sources, list) or not any(
            int(item.get("usable_candidate_count", 0)) > 0 for item in sources
        ):
            issues.append("no_source_contributed_usable_candidates")
    if args.overview:
        if not args.overview.is_file():
            issues.append("overview_missing")
        else:
            overview = args.overview.read_text(encoding="utf-8")
            required = (
                "领域问题概览与核心挑战", "主流方法路线", "跨方法比较",
                "共识、分歧与直接矛盾", "评测 Benchmark", "研究趋势与共同局限",
                "缺口与机会", "论文范围",
            )
            for heading in required:
                if not re.search(rf"^#+\s+{re.escape(heading)}\s*$", overview, re.MULTILINE):
                    issues.append(f"overview_section_missing:{heading}")
    if args.step8_results:
        step8 = read_json(args.step8_results, [])
        if not isinstance(step8, list):
            issues.append("step8_results_invalid")
        elif any(item.get("returncode") != 0 for item in step8):
            issues.append("step8_ingest_failed")
    payload = {"valid": not issues, "issues": sorted(set(issues)), "selected_checked": len(selected)}
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    p = commands.add_parser("build-reference-pool"); p.add_argument("--candidates", type=Path, required=True); p.add_argument("--topic-folder", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.set_defaults(func=build_reference_pool)
    p = commands.add_parser("filter-relevant"); p.add_argument("--candidates", type=Path, required=True); p.add_argument("--verification", type=Path, required=True); p.add_argument("--judgments", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.set_defaults(func=filter_relevant)
    p = commands.add_parser("build-score-manifest"); p.add_argument("--reference-pool", type=Path, required=True); p.add_argument("--candidates", type=Path, required=True); p.add_argument("--verification", type=Path, required=True); p.add_argument("--topic-folder", type=Path, required=True); p.add_argument("--judgments", type=Path, required=True); p.add_argument("--hf-results", type=Path); p.add_argument("--output", type=Path, required=True); p.set_defaults(func=build_score_manifest)
    p = commands.add_parser("select"); p.add_argument("--scores", type=Path, required=True); p.add_argument("--candidates", type=Path, required=True); p.add_argument("--verification", type=Path, required=True); p.add_argument("--topic-folder", type=Path, required=True); p.add_argument("--max-papers", type=int, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--exclusions-output", type=Path); p.set_defaults(func=select)
    p = commands.add_parser("refresh-selected-pdfs"); p.add_argument("--selected", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--min-bytes", type=int, default=10240); p.set_defaults(func=refresh_selected_pdfs)
    p = commands.add_parser("finalize-scores"); p.add_argument("--scores", type=Path, required=True); p.add_argument("--selected", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--patch-notes", action="store_true"); p.set_defaults(func=finalize_scores)
    p = commands.add_parser("run-step8"); p.add_argument("--scores", type=Path, required=True); p.add_argument("--wiki-root", type=Path, required=True); p.add_argument("--limit", type=int, default=20); p.add_argument("--output", type=Path, required=True); p.set_defaults(func=run_step8)
    p = commands.add_parser("audit-run"); p.add_argument("--reference-status", type=Path); p.add_argument("--coverage-threshold", type=float, default=0.95); p.add_argument("--selected", type=Path); p.add_argument("--scores", type=Path); p.add_argument("--final-scores", type=Path); p.add_argument("--source-status", type=Path); p.add_argument("--overview", type=Path); p.add_argument("--step8-results", type=Path); p.add_argument("--output", type=Path); p.set_defaults(func=audit_run)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
