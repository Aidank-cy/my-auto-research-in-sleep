#!/usr/bin/env python3
"""Prepare and validate note-only paper-analysis tasks for research-lit."""

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

from research_artifact_io import sha256_file, sha256_text, write_json  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402

NOTE_SECTIONS = ("Abstract", "Challenges", "Methodology", "Results", "Limitations", "Insights")
ARXIV_SUCCESS = {"downloaded_arxiv_pdf", "reused_arxiv_pdf", "reused_local_pdf"}
NOTE_SCHEMA_VERSION = 2
MIN_SECTION_CHARS = 40


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean(value: Any) -> str:
    return " ".join(str(value).strip().split()) if value not in (None, "") else ""


def author_list(value: Any) -> list[str]:
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                name = clean(item.get("name"))
            else:
                name = clean(item)
            if name:
                result.append(name)
        return result[:3]
    text = clean(value)
    return [part.strip() for part in re.split(r";|,", text) if part.strip()][:3]


def note_filename(candidate: dict[str, Any]) -> str:
    identity = clean(candidate.get("arxiv_id")) or clean(candidate.get("id")) or "paper"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-.").lower()
    return f"{safe or 'paper'}.md"


def load_optional_json(path: Path, default: Any) -> Any:
    try:
        return load_json(path) if path.is_file() else default
    except (OSError, json.JSONDecodeError):
        return default


def task_input_sha256(task: dict[str, Any], pdf_path: Path) -> str:
    identity = {
        "schema_version": NOTE_SCHEMA_VERSION,
        "id": task.get("id"),
        "arxiv_id": task.get("arxiv_id"),
        "title": task.get("title"),
        "authors": task.get("authors"),
        "published_date": task.get("published_date"),
        "venue": task.get("venue"),
        "discovery_source": task.get("discovery_source"),
        "verification_status": task.get("verification_status"),
        "verification_method": task.get("verification_method"),
        "mode": task.get("mode"),
        "pdf_sha256": task.get("pdf_sha256") or sha256_file(pdf_path),
    }
    return sha256_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))


def load_validated_wiki_notes(survey_root: Path) -> list[dict[str, Any]]:
    task_path = survey_root / "synthesis" / "paper_note_tasks.json"
    validation_path = survey_root / "synthesis" / "validation" / "paper_notes.json"
    wiki_path = survey_root / "synthesis" / "wiki_notes.json"
    if not task_path.is_file() or not validation_path.is_file() or not wiki_path.is_file():
        raise FileNotFoundError("paper_note_tasks.json, paper_notes.json, and wiki_notes.json are required")
    tasks_payload = load_json(task_path)
    validation = load_json(validation_path)
    wiki = load_json(wiki_path)
    if not isinstance(tasks_payload, dict) or not isinstance(validation, dict) or not isinstance(wiki, list):
        raise ValueError("invalid paper-note task, validation, or wiki routing artifact")
    if int(validation.get("invalid_count", -1)) != 0:
        raise ValueError("paper-note validation contains invalid notes")

    tasks = tasks_payload.get("tasks")
    records = validation.get("records")
    if not isinstance(tasks, list) or not isinstance(records, list):
        raise ValueError("paper-note tasks and validation records must be lists")
    task_by_id = {clean(task.get("id")): task for task in tasks if isinstance(task, dict) and clean(task.get("id"))}
    if len(task_by_id) != len(tasks):
        raise ValueError("paper-note tasks contain missing or duplicate IDs")
    valid_by_id = {clean(record.get("id")): record for record in records if isinstance(record, dict) and clean(record.get("id"))}
    if len(valid_by_id) != len(records) or any(record.get("status") != "valid" for record in records):
        raise ValueError("paper-note validation records must be unique and valid")
    if not (
        int(validation.get("task_count", -1))
        == int(validation.get("valid_count", -1))
        == len(records)
        == len(tasks)
    ):
        raise ValueError("paper-note task and validation counts do not match")

    for paper_id, record in valid_by_id.items():
        task = task_by_id.get(paper_id)
        if not task or task.get("status") != "reusable":
            raise ValueError(f"paper-note task is not reusable: {paper_id}")
        if clean(task.get("input_sha256")) != clean(record.get("input_sha256")):
            raise ValueError(f"paper-note input signature mismatch: {paper_id}")
        note_path = Path(clean(record.get("note_path")))
        pdf_path = Path(clean(task.get("pdf_path")))
        if note_path.resolve() != Path(clean(task.get("note_path"))).resolve() or not note_path.is_file():
            raise ValueError(f"paper-note path mismatch or missing note: {paper_id}")
        if clean(record.get("note_sha256")) != sha256_file(note_path):
            raise ValueError(f"paper note changed after validation: {paper_id}")
        if not pdf_path.is_file() or clean(task.get("pdf_sha256")) != sha256_file(pdf_path):
            raise ValueError(f"paper PDF changed after task preparation: {paper_id}")

    valid_ids = set(valid_by_id)
    routed_ids: set[str] = set()
    for record in wiki:
        if not isinstance(record, dict):
            raise ValueError("wiki_notes.json entries must be objects")
        paper_id = clean(record.get("paper_id"))
        note_path = Path(clean(record.get("note_path")))
        validation_record = valid_by_id.get(paper_id)
        if (
            not paper_id
            or paper_id in routed_ids
            or not note_path.is_file()
            or validation_record is None
            or note_path.resolve() != Path(clean(validation_record.get("note_path"))).resolve()
        ):
            raise ValueError(f"invalid wiki-note route for paper {paper_id or '<missing>'}")
        routed_ids.add(paper_id)
    if routed_ids != valid_ids:
        raise ValueError("wiki_notes.json does not exactly match valid paper-note records")
    return wiki


def prepare(survey_root: Path) -> dict[str, Any]:
    metadata = load_json(survey_root / "search" / "candidate_metadata.json")
    downloads = load_json(survey_root / "search" / "pdf_downloads.json")
    ranking = load_json(survey_root / "search" / "candidate_ranking.json")
    selected_ids = {
        clean(record.get("id")) for record in ranking.get("ranked_candidates", [])
        if isinstance(record, dict) and record.get("selected") is True
    }
    prior_validation = load_optional_json(survey_root / "synthesis" / "validation" / "paper_notes.json", {})
    prior_records = {
        clean(record.get("id")): record for record in prior_validation.get("records", [])
        if isinstance(record, dict) and clean(record.get("id"))
    }
    candidates = {
        clean(candidate.get("id")): candidate
        for candidate in metadata.get("candidates", [])
        if isinstance(candidate, dict) and clean(candidate.get("id"))
    }
    tasks: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    notes_dir = survey_root / "notes" / "papers"
    for record in downloads.get("records", []):
        candidate_id = clean(record.get("id"))
        candidate = candidates.get(candidate_id)
        reason = None
        if not candidate:
            reason = "candidate_missing"
        elif candidate_id not in selected_ids:
            reason = "candidate_not_selected"
        elif candidate.get("verification_status") != "verified":
            reason = "candidate_not_verified"
        elif not clean(candidate.get("arxiv_id")) or record.get("status") not in ARXIV_SUCCESS:
            reason = "not_a_successful_arxiv_pdf"
        elif not clean(record.get("path")):
            reason = "pdf_path_missing"
        if reason:
            skipped.append({"id": candidate_id, "reason": reason})
            continue

        pdf_path = Path(clean(record["path"])).resolve()
        note_path = (notes_dir / note_filename(candidate)).resolve()
        task = {
                "id": candidate_id,
                "arxiv_id": candidate.get("arxiv_id"),
                "title": candidate.get("title"),
                "authors": author_list(candidate.get("authors")),
                "published_date": candidate.get("published") or candidate.get("publicationDate"),
                "venue": candidate.get("venue"),
                "discovery_source": "arxiv",
                "verification_status": "verified",
                "verification_method": candidate.get("verification_method"),
                "pdf_path": str(pdf_path),
                "note_path": str(note_path),
                "mode": "note-only",
                "schema_version": NOTE_SCHEMA_VERSION,
            }
        task["pdf_sha256"] = sha256_file(pdf_path)
        task["input_sha256"] = task_input_sha256(task, pdf_path)
        task["prepared_note_sha256"] = sha256_file(note_path) if note_path.is_file() else None
        prior = prior_records.get(candidate_id, {})
        if not note_path.is_file():
            task.update(status="pending", reason="note_missing")
        elif validate_note(note_path, clean(candidate.get("title"))):
            task.update(status="pending", reason="note_invalid")
        elif prior.get("input_sha256") and prior.get("input_sha256") != task["input_sha256"]:
            task.update(status="pending", reason="stale_input")
        else:
            task.update(status="reusable", reason="validated_existing")
        tasks.append(task)
    report = {
        "survey_root": str(survey_root),
        "task_count": len(tasks),
        "pending_count": sum(task["status"] == "pending" for task in tasks),
        "tasks": tasks,
        "skipped": skipped,
    }
    write_json(survey_root / "synthesis" / "paper_note_tasks.json", report)
    return report


def validate_note(path: Path, title: str) -> list[str]:
    if not path.is_file():
        return ["note_missing"]
    text = path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    title_match = re.search(r"^##\s+(.+?)\s*$", text, re.M)
    if not title_match:
        errors.append("paper_title_heading_missing")
    elif title and clean(title_match.group(1)).casefold() != clean(title).casefold():
        errors.append("title_mismatch")
    positions: list[int] = []
    matches: list[re.Match[str]] = []
    for section in NOTE_SECTIONS:
        match = re.search(rf"^###\s+{re.escape(section)}\s*$", text, re.M)
        if not match:
            errors.append(f"section_missing:{section}")
        else:
            positions.append(match.start())
            matches.append(match)
    if len(positions) == len(NOTE_SECTIONS) and positions != sorted(positions):
        errors.append("section_order_invalid")
    section_headings = re.findall(r"^###\s+(.+?)\s*$", text, re.M)
    if section_headings != list(NOTE_SECTIONS):
        errors.append("section_schema_invalid")
    if len(matches) == len(NOTE_SECTIONS):
        for index, (section, match) in enumerate(zip(NOTE_SECTIONS, matches)):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = clean(text[match.end():end])
            if len(body) < MIN_SECTION_CHARS:
                errors.append(f"section_too_short:{section}")
        results_index = NOTE_SECTIONS.index("Results")
        results_end = matches[results_index + 1].start()
        results = text[matches[results_index].end():results_end]
        items = list(re.finditer(r"^####\s+\d+\.\s+.+$", results, re.M))
        if not items:
            errors.append("results_items_missing")
        for index, item in enumerate(items):
            end = items[index + 1].start() if index + 1 < len(items) else len(results)
            body = results[item.end():end]
            if not re.search(r"^>\s+\S", body, re.M) and "*[Placeholder for quote:" not in body \
                    and "[Quote unavailable in extracted text; manual PDF verification required.]" not in body:
                errors.append(f"result_quote_missing:{index + 1}")
    return errors


def validate(survey_root: Path) -> tuple[int, dict[str, Any]]:
    task_path = survey_root / "synthesis" / "paper_note_tasks.json"
    payload = load_json(task_path)
    records: list[dict[str, Any]] = []
    wiki_notes: list[dict[str, Any]] = []
    for task in payload.get("tasks", []):
        note_path = Path(task["note_path"])
        errors = validate_note(note_path, clean(task.get("title")))
        current_note_sha256 = sha256_file(note_path) if note_path.is_file() else None
        if (
            task.get("status") == "pending"
            and task.get("prepared_note_sha256")
            and task.get("prepared_note_sha256") == current_note_sha256
        ):
            errors.append(f"pending_note_unchanged:{task.get('reason') or 'unknown'}")
        status = "valid" if not errors else "invalid"
        records.append({
            "id": task.get("id"),
            "note_path": str(note_path),
            "status": status,
            "errors": errors,
            "input_sha256": task.get("input_sha256"),
            "note_sha256": current_note_sha256,
        })
        if not errors:
            task.update(status="reusable", reason="validated_note")
            wiki_notes.append(
                {
                    "id": f"paper:{task.get('id')}",
                    "paper_id": task.get("id"),
                    "arxiv_id": task.get("arxiv_id"),
                    "title": task.get("title"),
                    "note_path": str(note_path),
                    "status": "saved",
                    "mode": "note-only",
                }
            )
        else:
            reason = task.get("reason") if task.get("status") == "pending" else None
            task.update(status="pending", reason=reason or ("note_missing" if "note_missing" in errors else "note_invalid"))
    report = {
        "survey_root": str(survey_root),
        "task_count": len(records),
        "valid_count": sum(record["status"] == "valid" for record in records),
        "invalid_count": sum(record["status"] == "invalid" for record in records),
        "records": records,
    }
    write_json(survey_root / "synthesis" / "validation" / "paper_notes.json", report)
    write_json(survey_root / "synthesis" / "wiki_notes.json", wiki_notes)
    payload["pending_count"] = sum(task.get("status") == "pending" for task in payload.get("tasks", []))
    write_json(task_path, payload)
    return (0 if report["invalid_count"] == 0 else 2), report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("prepare", "validate"):
        command = sub.add_parser(name)
        add_survey_root_args(command)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
        if args.command == "prepare":
            report = prepare(survey_root)
            code = 0
        else:
            code, report = validate(survey_root)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
