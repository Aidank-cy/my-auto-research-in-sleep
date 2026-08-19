#!/usr/bin/env python3
"""Validate one paper-analysis artifact bundle against its source PDF."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

QUOTE_PLACEHOLDER = "[Quote unavailable in extracted text; manual PDF verification required.]"
REQUIRED_HEADINGS = ["Abstract", "Challenges", "Methodology", "Results", "Limitations", "Insights"]
TOKEN_RE = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return [token.replace("’", "'").casefold() for token in TOKEN_RE.findall(text)]


def quote_is_in_pdf(quote: str, pdf_text: str) -> bool:
    if quote == QUOTE_PLACEHOLDER:
        return True
    needle, haystack = _tokens(quote), _tokens(pdf_text)
    if not needle:
        return False
    size = len(needle)
    return any(haystack[index : index + size] == needle for index in range(len(haystack) - size + 1))


def _extract_pdf_text(pdf: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-raw", str(pdf), "-"], text=True, capture_output=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "pdftotext failed")
    return result.stdout


def _logic_ids(logic: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for section in ("challenges", "methodology"):
        ids.extend(str(item.get("id")) for item in logic.get(section, {}).get("key_insights", []))
    ids.extend(str(item.get("id")) for item in logic.get("results", {}).get("items", []))
    ids.extend(str(item.get("id")) for item in logic.get("limitations", {}).get("items", []))
    return ids


def validate_paper_folder(folder: Path, pdf_text: str | None = None) -> list[str]:
    issues: list[str] = []
    required = ["note.md", "note.json", "logic.json", "evidence.json"]
    for name in required:
        if not (folder / name).is_file():
            issues.append(f"missing_file:{name}")
    if issues:
        return issues

    try:
        note_meta = _load(folder / "note.json")
        logic = _load(folder / "logic.json")
        evidence = _load(folder / "evidence.json")
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid_json:{exc}"]
    note = (folder / "note.md").read_text(encoding="utf-8")

    paper_ids = {str(data.get("paper_id")) for data in (note_meta, logic, evidence)}
    if len(paper_ids) != 1 or "None" in paper_ids:
        issues.append("paper_id_mismatch")

    for field in ("title", "authors", "published_date", "discovery_source", "verification_status", "verification_method", "paper_type"):
        if field not in note_meta:
            issues.append(f"note_json_missing:{field}")

    logic_ids = _logic_ids(logic)
    if len(logic_ids) != len(set(logic_ids)):
        issues.append("duplicate_logic_id")
    expected_ki = [f"KI-{i}" for i in range(1, 1 + sum(value.startswith("KI-") for value in logic_ids))]
    expected_r = [f"R{i}" for i in range(1, 1 + sum(value.startswith("R") for value in logic_ids))]
    expected_l = [f"L{i}" for i in range(1, 1 + sum(value.startswith("L") for value in logic_ids))]
    for expected, prefix in ((expected_ki, "KI"), (expected_r, "R"), (expected_l, "L")):
        actual = [value for value in logic_ids if value.startswith(prefix)]
        if actual != expected:
            issues.append(f"nonsequential_ids:{prefix}")

    entries = evidence.get("entries")
    if not isinstance(entries, dict):
        issues.append("evidence_entries_not_object")
        entries = {}
    if set(logic_ids) != set(entries):
        issues.append("evidence_id_coverage_mismatch")

    pdfs = sorted(folder.glob("*.pdf"))
    if pdf_text is None:
        if len(pdfs) != 1:
            issues.append(f"pdf_count:{len(pdfs)}")
            pdf_text = ""
        else:
            try:
                pdf_text = _extract_pdf_text(pdfs[0])
            except RuntimeError as exc:
                issues.append(f"pdf_text_error:{exc}")
                pdf_text = ""

    for entry_id, entry in entries.items():
        if not isinstance(entry, dict):
            issues.append(f"invalid_evidence_entry:{entry_id}")
            continue
        quote = entry.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            issues.append(f"missing_quote:{entry_id}")
        elif not quote_is_in_pdf(quote, pdf_text or ""):
            issues.append(f"quote_not_in_pdf:{entry_id}")
        elif quote not in note:
            issues.append(f"quote_missing_from_note:{entry_id}")
        images = entry.get("images")
        if not isinstance(images, list):
            issues.append(f"images_not_array:{entry_id}")
            continue
        for image in images:
            status = image.get("status")
            ref = image.get("image_ref")
            source = image.get("source_figure")
            if status == "resolved":
                if not ref or not (folder / str(ref)).is_file() or str(ref) not in note:
                    issues.append(f"resolved_image_mismatch:{entry_id}:{source}")
            elif status == "unresolved":
                if ref is not None:
                    issues.append(f"unresolved_image_has_ref:{entry_id}:{source}")
                placeholder = f"*[Placeholder for {source}: {image.get('caption', '')}]*"
                if placeholder not in note:
                    issues.append(f"unresolved_placeholder_missing:{entry_id}:{source}")
            else:
                issues.append(f"invalid_image_status:{entry_id}:{status}")

    titles = re.findall(r"^## (.+?)\s*$", note, flags=re.MULTILINE)
    if len(titles) != 1:
        issues.append("note_title_count")
    headings = re.findall(r"^### (.+?)\s*$", note, flags=re.MULTILINE)
    if headings != REQUIRED_HEADINGS:
        issues.append("note_heading_order")
    result_count = len(logic.get("results", {}).get("items", []))
    note_result_count = len(re.findall(r"^#### \d+\. ", note, flags=re.MULTILINE))
    if result_count != note_result_count:
        issues.append("result_heading_count_mismatch")
    if not re.search(r"^\*Authors: .+ · Published: .+ · Source: .+\*$", note, flags=re.MULTILINE):
        issues.append("metadata_line_missing")
    if not re.search(r"^\*Verification: .+\*$", note, flags=re.MULTILINE):
        issues.append("verification_line_missing")
    if "<div" in note.lower() or "<img" in note.lower():
        issues.append("raw_html_image")

    formula_groups = [logic.get("challenges", {}).get("formulas", []), logic.get("methodology", {}).get("formulas", [])]
    formula_groups.extend(item.get("formulas", []) for item in logic.get("results", {}).get("items", []))
    for group in formula_groups:
        if not isinstance(group, list):
            issues.append("formulas_not_array")
            continue
        for formula in group:
            latex = formula.get("latex") if isinstance(formula, dict) else None
            if not isinstance(latex, str) or not latex.strip() or "$$" in latex:
                issues.append("invalid_formula_latex")

    lines = note.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^!\[.*\]\(images/.+\)$", line):
            before = lines[index - 1] if index else ""
            after = lines[index + 1] if index + 1 < len(lines) else ""
            if before.strip() or (after.strip() and after.startswith("!")):
                issues.append(f"image_not_isolated:line_{index + 1}")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_folder", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    issues = validate_paper_folder(args.paper_folder)
    payload = {"paper_folder": str(args.paper_folder), "valid": not issues, "issues": issues}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else ("PASS" if not issues else "\n".join(issues)))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
