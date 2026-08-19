#!/usr/bin/env python3
"""Validate model-written synthesis Markdown and build machine projections."""

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

import research_relation_validate  # noqa: E402
import research_synthesis_nodes  # noqa: E402
from research_artifact_io import sha256_file, sha256_text, write_json, write_jsonl, atomic_write_text  # noqa: E402
from research_packet_io import load_packet_bundle  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402


NODE_FIELDS = {
    "evidence": ["Finding", "Context", "Evidence type", "Limitations", "Tags", "Provenance"],
    "claim": ["Statement", "Status", "Explanation", "Evidence", "Scope", "Tags", "Provenance"],
    "problem": [
        "Current state",
        "Desired state",
        "Gap",
        "Status",
        "Explanation",
        "Importance",
        "Evidence",
        "Scope",
        "Tags",
        "Provenance",
    ],
    "heuristic": [
        "Prescription",
        "Rationale",
        "Targets problem",
        "Status",
        "Evidence",
        "Scope",
        "Tags",
        "Provenance",
    ],
}
NODE_MARKERS = {"evidence": "E", "claim": "C", "problem": "P", "heuristic": "H"}
VALID_STATUS = {
    "claim": {"hypothesis", "supported", "refuted"},
    "problem": {"open", "partially-addressed", "contested", "resolved"},
    "heuristic": {"proposed", "supported", "contested", "invalidated"},
}
VALID_EVIDENCE_TYPES = {
    "empirical-result",
    "theoretical-result",
    "source-statement",
    "secondary-report",
    "metadata",
}
CORE_FIELDS = {
    "evidence": ["Finding", "Evidence type", "Provenance"],
    "claim": ["Statement", "Status", "Evidence", "Scope", "Provenance"],
    "problem": ["Current state", "Desired state", "Gap", "Status", "Importance", "Evidence", "Scope", "Provenance"],
    "heuristic": ["Prescription", "Targets problem", "Status", "Evidence", "Scope", "Provenance"],
}
VALID_SCOPES = {"paper-local", "domain-level"}
MATCH_FIELDS = {
    "claim": ["Statement"],
    "problem": ["Current state", "Desired state", "Gap"],
    "heuristic": ["Prescription", "Targets problem"],
}
HEADING_RE = re.compile(
    r"^###\s+([CEPH])(\d+):\s+(.+?)\s+\^(evidence|claim|problem|heuristic)-([a-z0-9][a-z0-9-]*)\s*$"
)
FIELD_RE = re.compile(r"^- \*\*(.+?)\*\*:\s*(.*)$")
BLOCK_REF_RE = re.compile(r"\^((?:evidence|claim|problem|heuristic)-[a-z0-9][a-z0-9-]*)")
PAPER_NODE_RE = re.compile(r"\bpaper:([A-Za-z0-9._-]+)\b")
TAG_RE = re.compile(r"#([a-z0-9][a-z0-9-]*)")
CLUSTER_HEADING_RE = re.compile(
    r"^###\s+(C|P|H)CL(\d+):\s+(.+?)\s+\^cluster-(claim|problem|heuristic)-([a-z0-9][a-z0-9-]*)\s*$"
)
EXCLUSION_RE = re.compile(r"^-\s+.*?\^((?:claim|problem|heuristic)-[a-z0-9][a-z0-9-]*).*?:\s*(.+)$")
RELATION_HEADING_RE = re.compile(r"^###\s+R(\d+):\s+(.+?)\s+\^relation-([a-z0-9][a-z0-9-]*)\s*$")
RELATION_FIELDS = [
    "From",
    "Type",
    "To",
    "Evidence",
    "Confidence",
    "Source packets",
    "Source nodes",
    "Evidence level",
    "Read scope",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def field_key(label: str) -> str:
    return label.lower().replace(" ", "_")


def block_to_node(block_id: str) -> str:
    prefix, slug = block_id.split("-", 1)
    return f"{prefix}:{slug}"


def refs(value: str) -> list[str]:
    return [block_to_node(block_id) for block_id in BLOCK_REF_RE.findall(value)]


def paper_ids(value: str) -> list[str]:
    return PAPER_NODE_RE.findall(value)


def tags(value: str) -> list[str]:
    return TAG_RE.findall(value)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_source_block(survey_root: Path, path: Path, block_id: str) -> str:
    try:
        relative = path.relative_to(survey_root)
    except ValueError:
        relative = path
    return f"{relative.as_posix()}#^{block_id}"


def parse_markdown_nodes(path: Path, survey_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return [], [{"code": "missing_file", "file": str(path), "message": "Markdown file does not exist"}]
    lines = path.read_text(encoding="utf-8").splitlines()
    nodes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        kind = current["kind"]
        expected = NODE_FIELDS[kind]
        actual = list(current["fields"])
        if actual != expected:
            errors.append(
                {
                    "code": "field_schema_mismatch",
                    "file": str(path),
                    "line": current["line"],
                    "node_id": current["node_id"],
                    "message": f"expected fields {expected}, got {actual}",
                }
            )
        for label in CORE_FIELDS[kind]:
            if not clean_text(current["fields"].get(label)):
                errors.append(
                    {
                        "code": "empty_required_field",
                        "file": str(path),
                        "line": current["line"],
                        "node_id": current["node_id"],
                        "message": f"{label} must be non-empty",
                    }
                )
        node_tags = tags(current["fields"].get("Tags", ""))
        if not 1 <= len(node_tags) <= 5:
            errors.append(
                {
                    "code": "invalid_tags",
                    "file": str(path),
                    "line": current["line"],
                    "node_id": current["node_id"],
                    "message": "Tags must contain one to five lowercase kebab-case Obsidian tags",
                }
            )
        current["tags"] = node_tags
        nodes.append(current)
        current = None

    for line_number, line in enumerate(lines, start=1):
        heading = HEADING_RE.match(line)
        if heading:
            finish()
            marker, number, title, kind, slug = heading.groups()
            block_id = f"{kind}-{slug}"
            current = {
                "kind": kind,
                "marker": marker,
                "number": int(number),
                "title": title.strip(),
                "block_id": block_id,
                "node_id": block_to_node(block_id),
                "fields": {},
                "line": line_number,
                "source_block": relative_source_block(survey_root, path, block_id),
            }
            if marker != NODE_MARKERS[kind]:
                errors.append(
                    {
                        "code": "kind_marker_mismatch",
                        "file": str(path),
                        "line": line_number,
                        "node_id": current["node_id"],
                        "message": f"{kind} heading must use {NODE_MARKERS[kind]}",
                    }
                )
            continue
        if current is None:
            continue
        field = FIELD_RE.match(line)
        if field:
            label, value = field.groups()
            if label in current["fields"]:
                errors.append(
                    {
                        "code": "duplicate_field",
                        "file": str(path),
                        "line": line_number,
                        "node_id": current["node_id"],
                        "message": f"duplicate field: {label}",
                    }
                )
            current["fields"][label] = value.strip()
        elif line.strip() and not line.startswith("## "):
            errors.append(
                {
                    "code": "unexpected_node_content",
                    "file": str(path),
                    "line": line_number,
                    "node_id": current["node_id"],
                    "message": "node content must use one-line bold fields",
                }
            )
    finish()

    seen: set[str] = set()
    numbers: dict[str, list[int]] = {kind: [] for kind in NODE_FIELDS}
    for node in nodes:
        if node["node_id"] in seen:
            errors.append(
                {
                    "code": "duplicate_node_id",
                    "file": str(path),
                    "line": node["line"],
                    "node_id": node["node_id"],
                    "message": "node ID must be unique",
                }
            )
        seen.add(node["node_id"])
        numbers[node["kind"]].append(node["number"])
        if node["kind"] == "evidence":
            evidence_type = clean_text(node["fields"].get("Evidence type"))
            if evidence_type not in VALID_EVIDENCE_TYPES:
                errors.append(
                    {
                        "code": "invalid_evidence_type",
                        "file": str(path),
                        "line": node["line"],
                        "node_id": node["node_id"],
                        "message": f"invalid Evidence type: {evidence_type}",
                    }
                )
        else:
            status = clean_text(node["fields"].get("Status"))
            if status not in VALID_STATUS[node["kind"]]:
                errors.append(
                    {
                        "code": "invalid_status",
                        "file": str(path),
                        "line": node["line"],
                        "node_id": node["node_id"],
                        "message": f"invalid {node['kind']} status: {status}",
                    }
                )
    for kind, values in numbers.items():
        if values and values != list(range(1, len(values) + 1)):
            errors.append(
                {
                    "code": "invalid_numbering",
                    "file": str(path),
                    "message": f"{kind} headings must be numbered consecutively from 1",
                }
            )
    return nodes, errors


def projection(node: dict[str, Any], *, source_block: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "node_id": node["node_id"],
        "kind": node["kind"],
        "title": node["title"],
    }
    for label, value in node["fields"].items():
        key = field_key(label)
        if label == "Evidence":
            result["evidence_nodes"] = refs(value)
        elif label == "Targets problem":
            result["target_problem_nodes"] = refs(value)
        elif label == "Tags":
            result["tags"] = tags(value)
        else:
            result[key] = value
    result["source_block"] = source_block or node["source_block"]
    return result


def render_nodes(title: str, section: str, nodes: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", "", f"## {section}", ""]
    for index, node in enumerate(nodes, start=1):
        lines.append(f"### {NODE_MARKERS[node['kind']]}{index}: {node['title']} ^{node['block_id']}")
        lines.append("")
        for label in NODE_FIELDS[node["kind"]]:
            lines.append(f"- **{label}**: {node['fields'].get(label, '')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extraction_metadata(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            break
        match = FIELD_RE.match(line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def bundle_hash(paths: list[Path]) -> str:
    parts = [f"{path}:{sha256_file(path)}" for path in paths]
    return sha256_text("|".join(parts))


def load_manifest(survey_root: Path) -> tuple[Path, dict[str, Any]]:
    path = survey_root / "synthesis" / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    manifest = load_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != research_synthesis_nodes.SCHEMA_VERSION:
        raise ValueError("manifest schema is not current; rerun research_synthesis_nodes.py")
    return path, manifest


def verify_packet_inputs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
    for name in ("candidate_metadata", "candidate_ranking", "packet_index"):
        descriptor = inputs.get(name) if isinstance(inputs.get(name), dict) else {}
        path = Path(clean_text(descriptor.get("path")))
        if not path.exists():
            errors.append({"code": "missing_input", "message": f"{name} is missing: {path}"})
        elif clean_text(descriptor.get("sha256")) != sha256_file(path):
            errors.append({"code": "stale_input", "message": f"{name} changed after scaffolding"})
    if errors:
        return errors
    _, _, _, validation_errors = research_synthesis_nodes.validate_inputs(
        Path(inputs["candidate_metadata"]["path"]),
        Path(inputs["candidate_ranking"]["path"]),
        Path(inputs["packet_index"]["path"]),
    )
    return validation_errors


def downstream_status(manifest: dict[str, Any], stage: str) -> str:
    current = manifest.get("stages", {}).get(stage, {}).get("status")
    return "stale" if current in {"valid", "invalid", "stale"} else "pending"


def write_stage_report(survey_root: Path, stage: str, report: dict[str, Any]) -> Path:
    path = survey_root / "synthesis" / "validation" / f"{stage}.json"
    write_json(path, report)
    return path


def fail_stage(
    survey_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    stage: str,
    errors: list[dict[str, Any]],
) -> int:
    report = {"valid": False, "stage": stage, "errors": errors}
    report_path = write_stage_report(survey_root, stage, report)
    manifest["stages"][stage] = {"status": "invalid", "updated_at": utc_now(), "report": str(report_path)}
    stage_order = ["extraction", "evidence", "canonical", "projections", "relations", "review", "query_pack"]
    if stage in stage_order:
        for downstream in stage_order[stage_order.index(stage) + 1 :]:
            manifest["stages"][downstream] = {
                "status": downstream_status(manifest, downstream),
                "updated_at": utc_now(),
            }
    write_json(manifest_path, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


def compile_extraction(survey_root: Path, manifest_path: Path, manifest: dict[str, Any]) -> int:
    errors = verify_packet_inputs(manifest)
    if errors:
        manifest["stages"]["packets"]["status"] = "stale"
        return fail_stage(survey_root, manifest_path, manifest, "extraction", errors)
    _, packet_records = load_packet_bundle(Path(manifest["inputs"]["packet_index"]["path"]))
    packets = {clean_text(item.get("id")): item for item in packet_records}
    extraction_paths = [Path(path) for path in manifest["outputs"]["extraction_files"]]
    all_nodes: list[dict[str, Any]] = []
    evidence_nodes: list[dict[str, Any]] = []
    logic_nodes: list[dict[str, Any]] = []
    global_ids: set[str] = set()

    for path in extraction_paths:
        if not path.exists():
            errors.append({"code": "missing_extraction", "file": str(path), "message": "extraction file is missing"})
            continue
        metadata = extraction_metadata(path)
        paper_id = clean_text(metadata.get("Paper ID"))
        packet = packets.get(paper_id)
        if packet is None:
            errors.append({"code": "unknown_extraction_paper", "file": str(path), "message": f"unknown Paper ID: {paper_id}"})
            continue
        expected_metadata = {
            "Source kind": clean_text(packet.get("source_kind")),
            "Evidence level": clean_text(packet.get("evidence_level")),
            "Read scope": clean_text(packet.get("read_scope")),
            "Packet SHA256": clean_text(packet.get("text_sha256")),
            "Extraction status": "complete",
        }
        for label, expected in expected_metadata.items():
            if clean_text(metadata.get(label)) != expected:
                errors.append(
                    {
                        "code": "extraction_metadata_mismatch",
                        "file": str(path),
                        "message": f"{label} must be {expected!r}",
                    }
                )
        nodes, node_errors = parse_markdown_nodes(path, survey_root)
        errors.extend(node_errors)
        local_ids = {node["node_id"] for node in nodes}
        evidence_ids = {node["node_id"] for node in nodes if node["kind"] == "evidence"}
        problem_ids = {node["node_id"] for node in nodes if node["kind"] == "problem"}
        for node in nodes:
            expected_prefix = f"{node['kind']}:{paper_id}-"
            if not node["node_id"].startswith(expected_prefix):
                errors.append(
                    {
                        "code": "paper_local_id_mismatch",
                        "file": str(path),
                        "line": node["line"],
                        "node_id": node["node_id"],
                        "message": f"extraction node ID must start with {expected_prefix}",
                    }
                )
            if node["node_id"] in global_ids:
                errors.append({"code": "duplicate_global_node_id", "node_id": node["node_id"], "message": "node ID repeats across papers"})
            global_ids.add(node["node_id"])
            provenance = clean_text(node["fields"].get("Provenance"))
            if f"paper:{paper_id}" not in provenance:
                errors.append(
                    {
                        "code": "missing_paper_provenance",
                        "file": str(path),
                        "line": node["line"],
                        "node_id": node["node_id"],
                        "message": f"Provenance must include paper:{paper_id}",
                    }
                )
            if (
                clean_text(metadata.get("Evidence level")) == "metadata-only"
                and node["kind"] == "evidence"
                and clean_text(node["fields"].get("Evidence type")) != "metadata"
            ):
                errors.append(
                    {
                        "code": "invalid_metadata_evidence_type",
                        "file": str(path),
                        "line": node["line"],
                        "node_id": node["node_id"],
                        "message": "metadata-only packets require Evidence type metadata",
                    }
                )
            if node["kind"] != "evidence":
                scope = clean_text(node["fields"].get("Scope"))
                if scope not in VALID_SCOPES:
                    errors.append(
                        {
                            "code": "invalid_extraction_scope",
                            "file": str(path),
                            "line": node["line"],
                            "node_id": node["node_id"],
                            "message": f"extraction Logic nodes must use one of {sorted(VALID_SCOPES)}",
                        }
                    )
                evidence_refs = refs(node["fields"].get("Evidence", ""))
                if not evidence_refs or any(ref not in evidence_ids for ref in evidence_refs):
                    errors.append(
                        {
                            "code": "invalid_evidence_link",
                            "file": str(path),
                            "line": node["line"],
                            "node_id": node["node_id"],
                            "message": "Evidence links must resolve to Evidence in the same extraction file",
                        }
                    )
                if node["kind"] == "heuristic":
                    target_refs = refs(node["fields"].get("Targets problem", ""))
                    if not target_refs or any(ref not in problem_ids for ref in target_refs):
                        errors.append(
                            {
                                "code": "invalid_problem_link",
                                "file": str(path),
                                "line": node["line"],
                                "node_id": node["node_id"],
                                "message": "Targets problem must resolve in the same extraction file",
                            }
                        )
            node["paper_id"] = paper_id
            all_nodes.append(node)
            (evidence_nodes if node["kind"] == "evidence" else logic_nodes).append(node)
        unknown_refs = {ref for node in nodes for value in node["fields"].values() for ref in refs(value) if ref not in local_ids}
        for unknown in sorted(unknown_refs):
            errors.append({"code": "unresolved_local_link", "file": str(path), "node_id": unknown, "message": "link does not resolve in extraction file"})

    if errors:
        return fail_stage(survey_root, manifest_path, manifest, "extraction", errors)

    evidence_path = Path(manifest["outputs"]["evidence_markdown"])
    evidence_text = render_nodes("Literature Evidence", "Evidence", evidence_nodes)
    atomic_write_text(evidence_path, evidence_text)
    canonical_evidence, render_errors = parse_markdown_nodes(evidence_path, survey_root)
    if render_errors:
        return fail_stage(survey_root, manifest_path, manifest, "evidence", render_errors)
    evidence_jsonl = [projection(node) for node in canonical_evidence]
    evidence_jsonl_path = Path(manifest["outputs"]["evidence"])
    write_jsonl(evidence_jsonl_path, evidence_jsonl)

    candidate_index: list[dict[str, Any]] = []
    for node in logic_nodes:
        item = projection(node)
        item.update(
            paper_id=node["paper_id"],
            match_fields={field_key(label): node["fields"].get(label, "") for label in MATCH_FIELDS[node["kind"]]},
        )
        candidate_index.append(item)
    candidate_index_path = Path(manifest["outputs"]["candidate_index"])
    write_jsonl(candidate_index_path, candidate_index)

    extraction_hash = bundle_hash(extraction_paths)
    previous_hash = clean_text(manifest["stages"].get("extraction", {}).get("output_sha256"))
    report = {
        "valid": True,
        "stage": "extraction",
        "paper_count": len(extraction_paths),
        "node_count": len(all_nodes),
        "evidence_count": len(evidence_nodes),
        "logic_candidate_count": len(logic_nodes),
        "errors": [],
    }
    report_path = write_stage_report(survey_root, "extraction", report)
    now = utc_now()
    manifest["stages"]["extraction"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": manifest["pipeline_input_sha256"],
        "output_sha256": extraction_hash,
        "report": str(report_path),
    }
    manifest["stages"]["evidence"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": extraction_hash,
        "output_hashes": {
            "evidence_markdown": sha256_file(evidence_path),
            "evidence_jsonl": sha256_file(evidence_jsonl_path),
            "candidate_index": sha256_file(candidate_index_path),
        },
    }
    if previous_hash != extraction_hash:
        for stage in ("canonical", "projections", "relations", "review", "query_pack"):
            manifest["stages"][stage] = {"status": downstream_status(manifest, stage), "updated_at": now}
    write_json(manifest_path, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parse_clusters(path: Path) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    if not path.exists():
        return [], {}, [{"code": "missing_file", "file": str(path), "message": "clusters.md is missing"}]
    lines = path.read_text(encoding="utf-8").splitlines()
    clusters: list[dict[str, Any]] = []
    exclusions: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_exclusions = False

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        expected = ["Members", "Match fields", "Canonical node", "Reason"]
        actual = list(current["fields"])
        if actual != expected:
            errors.append(
                {
                    "code": "cluster_field_schema_mismatch",
                    "file": str(path),
                    "line": current["line"],
                    "message": f"expected fields {expected}, got {actual}",
                }
            )
        for label in expected:
            if not clean_text(current["fields"].get(label)):
                errors.append(
                    {
                        "code": "empty_cluster_field",
                        "file": str(path),
                        "line": current["line"],
                        "message": f"{label} must be non-empty",
                    }
                )
        clusters.append(current)
        current = None

    for line_number, line in enumerate(lines, start=1):
        if line == "## Excluded Candidates":
            finish()
            in_exclusions = True
            continue
        heading = CLUSTER_HEADING_RE.match(line)
        if heading:
            finish()
            in_exclusions = False
            marker, number, title, kind, slug = heading.groups()
            if marker != NODE_MARKERS[kind]:
                errors.append({"code": "cluster_kind_marker_mismatch", "file": str(path), "line": line_number, "message": "cluster marker disagrees with kind"})
            current = {
                "kind": kind,
                "number": int(number),
                "title": title,
                "block_id": f"cluster-{kind}-{slug}",
                "fields": {},
                "line": line_number,
            }
            continue
        if in_exclusions and line.strip().startswith("-"):
            match = EXCLUSION_RE.match(line)
            if not match:
                errors.append({"code": "invalid_exclusion", "file": str(path), "line": line_number, "message": "exclusion must link one candidate and give a reason"})
                continue
            node_id = block_to_node(match.group(1))
            if node_id in exclusions:
                errors.append({"code": "duplicate_exclusion", "file": str(path), "line": line_number, "node_id": node_id, "message": "candidate is excluded more than once"})
            exclusions[node_id] = match.group(2).strip()
            continue
        if current is not None:
            field = FIELD_RE.match(line)
            if field:
                label = field.group(1)
                if label in current["fields"]:
                    errors.append({"code": "duplicate_cluster_field", "file": str(path), "line": line_number, "message": f"duplicate field: {label}"})
                current["fields"][label] = field.group(2).strip()
            elif line.strip() and not line.startswith("## "):
                errors.append({"code": "unexpected_cluster_content", "file": str(path), "line": line_number, "message": "cluster content must use one-line bold fields"})
    finish()
    seen_blocks: set[str] = set()
    numbers: dict[str, list[int]] = {kind: [] for kind in MATCH_FIELDS}
    for cluster in clusters:
        if cluster["block_id"] in seen_blocks:
            errors.append({"code": "duplicate_cluster_id", "file": str(path), "line": cluster["line"], "message": "cluster block ID must be unique"})
        seen_blocks.add(cluster["block_id"])
        numbers[cluster["kind"]].append(cluster["number"])
    for kind, values in numbers.items():
        if values and values != list(range(1, len(values) + 1)):
            errors.append({"code": "invalid_cluster_numbering", "file": str(path), "message": f"{kind} clusters must be numbered consecutively from 1"})
    return clusters, exclusions, errors


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        records.append(value)
    return records


def compile_canonical(survey_root: Path, manifest_path: Path, manifest: dict[str, Any]) -> int:
    errors = verify_packet_inputs(manifest)
    if manifest["stages"].get("extraction", {}).get("status") != "valid":
        errors.append({"code": "extraction_not_valid", "message": "run extraction compilation first"})
    extraction_paths = [Path(path) for path in manifest["outputs"]["extraction_files"]]
    if all(path.exists() for path in extraction_paths):
        current_hash = bundle_hash(extraction_paths)
        if current_hash != manifest["stages"].get("extraction", {}).get("output_sha256"):
            errors.append({"code": "stale_extraction", "message": "extraction Markdown changed after validation"})
    evidence_stage = manifest["stages"].get("evidence", {})
    evidence_output_paths = {
        "evidence_markdown": Path(manifest["outputs"]["evidence_markdown"]),
        "evidence_jsonl": Path(manifest["outputs"]["evidence"]),
        "candidate_index": Path(manifest["outputs"]["candidate_index"]),
    }
    for name, expected_hash in evidence_stage.get("output_hashes", {}).items():
        path = evidence_output_paths.get(name, Path("__missing__"))
        if not path.exists() or sha256_file(path) != expected_hash:
            errors.append({"code": "stale_evidence_output", "message": f"{name} changed after extraction compilation"})
    if errors:
        return fail_stage(survey_root, manifest_path, manifest, "canonical", errors)

    candidate_path = Path(manifest["outputs"]["candidate_index"])
    candidates = read_jsonl(candidate_path)
    candidates_by_id = {item["node_id"]: item for item in candidates}
    clusters_path = Path(manifest["outputs"]["clusters"])
    clusters, exclusions, cluster_errors = parse_clusters(clusters_path)
    errors.extend(cluster_errors)
    logic_path = Path(manifest["outputs"]["logic_markdown"])
    logic_nodes, logic_errors = parse_markdown_nodes(logic_path, survey_root)
    errors.extend(logic_errors)
    logic_nodes = [node for node in logic_nodes if node["kind"] in {"claim", "problem", "heuristic"}]
    logic_by_id = {node["node_id"]: node for node in logic_nodes}

    represented: dict[str, str] = {}
    canonical_refs: set[str] = set()
    for cluster in clusters:
        members = refs(cluster["fields"].get("Members", ""))
        canonical = refs(cluster["fields"].get("Canonical node", ""))
        expected_match = "; ".join(MATCH_FIELDS[cluster["kind"]])
        if clean_text(cluster["fields"].get("Match fields")) != expected_match:
            errors.append({"code": "invalid_match_fields", "line": cluster["line"], "message": f"{cluster['kind']} Match fields must be {expected_match}"})
        if not members:
            errors.append({"code": "empty_decision_members", "line": cluster["line"], "message": "each canonical decision needs at least one member"})
        if len(canonical) != 1:
            errors.append({"code": "invalid_canonical_link", "line": cluster["line"], "message": "each canonical decision must link exactly one canonical node"})
        else:
            if canonical[0] in canonical_refs:
                errors.append({"code": "duplicate_canonical_mapping", "line": cluster["line"], "node_id": canonical[0], "message": "canonical node appears in multiple decisions"})
            canonical_refs.add(canonical[0])
            if not canonical[0].startswith(f"{cluster['kind']}:"):
                errors.append({"code": "canonical_kind_mismatch", "line": cluster["line"], "message": "canonical node kind disagrees with decision"})
        member_papers: set[str] = set()
        member_evidence: set[str] = set()
        valid_members: list[dict[str, Any]] = []
        for member in members:
            candidate = candidates_by_id.get(member)
            if candidate is None:
                errors.append({"code": "unknown_cluster_member", "line": cluster["line"], "node_id": member, "message": "decision member is not in candidate index"})
                continue
            valid_members.append(candidate)
            if candidate.get("kind") != cluster["kind"]:
                errors.append({"code": "cluster_member_kind_mismatch", "line": cluster["line"], "node_id": member, "message": "canonical decision mixes node kinds"})
            if member in represented:
                errors.append({"code": "duplicate_candidate_coverage", "line": cluster["line"], "node_id": member, "message": "candidate appears in multiple cluster decisions"})
            represented[member] = cluster["block_id"]
            member_papers.add(clean_text(candidate.get("paper_id")))
            member_evidence.update(candidate.get("evidence_nodes") or [])
        if len(valid_members) == 1 and clean_text(valid_members[0].get("scope")) != "domain-level":
            errors.append(
                {
                    "code": "invalid_single_member_scope",
                    "line": cluster["line"],
                    "node_id": valid_members[0].get("node_id"),
                    "message": "a single-member canonical decision requires a domain-level candidate",
                }
            )
        if canonical:
            node = logic_by_id.get(canonical[0])
            if node is not None:
                actual_evidence = set(refs(node["fields"].get("Evidence", "")))
                if actual_evidence != member_evidence:
                    errors.append({"code": "canonical_evidence_mismatch", "node_id": node["node_id"], "message": "canonical Evidence links must equal the union of member Evidence links"})
                provenance_papers = set(paper_ids(node["fields"].get("Provenance", "")))
                if not member_papers.issubset(provenance_papers):
                    errors.append({"code": "canonical_provenance_mismatch", "node_id": node["node_id"], "message": "canonical Provenance must include every member paper"})

    for node_id, reason in exclusions.items():
        if node_id not in candidates_by_id:
            errors.append({"code": "unknown_exclusion", "node_id": node_id, "message": "excluded node is not in candidate index"})
        if node_id in represented:
            errors.append({"code": "duplicate_candidate_coverage", "node_id": node_id, "message": "candidate is both clustered and excluded"})
        if not reason:
            errors.append({"code": "empty_exclusion_reason", "node_id": node_id, "message": "excluded candidate needs a reason"})
        represented[node_id] = "excluded"
    missing = sorted(set(candidates_by_id) - set(represented))
    if missing:
        errors.append({"code": "missing_candidate_coverage", "node_ids": missing, "message": "every candidate must be mapped or explicitly excluded"})
    extra_logic = sorted(set(logic_by_id) - canonical_refs)
    missing_logic = sorted(canonical_refs - set(logic_by_id))
    if extra_logic:
        errors.append({"code": "unmapped_canonical_nodes", "node_ids": extra_logic, "message": "every canonical Logic node must come from one decision"})
    if missing_logic:
        errors.append({"code": "missing_canonical_nodes", "node_ids": missing_logic, "message": "decision canonical links must resolve in logic.md"})

    evidence_nodes = {item["node_id"] for item in read_jsonl(Path(manifest["outputs"]["evidence"]))}
    for node in logic_nodes:
        if clean_text(node["fields"].get("Scope")) != "domain-level":
            errors.append({"code": "invalid_canonical_scope", "node_id": node["node_id"], "message": "canonical Logic nodes must use domain-level Scope"})
        linked = refs(node["fields"].get("Evidence", ""))
        if not linked or any(item not in evidence_nodes for item in linked):
            errors.append({"code": "invalid_canonical_evidence_link", "node_id": node["node_id"], "message": "canonical Evidence links must resolve"})
    problem_nodes = {node["node_id"] for node in logic_nodes if node["kind"] == "problem"}
    for node in logic_nodes:
        if node["kind"] == "heuristic":
            targets = refs(node["fields"].get("Targets problem", ""))
            if not targets or any(item not in problem_nodes for item in targets):
                errors.append({"code": "invalid_canonical_problem_link", "node_id": node["node_id"], "message": "canonical Targets problem links must resolve"})
    if errors:
        return fail_stage(survey_root, manifest_path, manifest, "canonical", errors)

    by_kind = {kind: [projection(node) for node in logic_nodes if node["kind"] == kind] for kind in ("claim", "problem", "heuristic")}
    output_paths = {
        "claims": Path(manifest["outputs"]["claims"]),
        "problems": Path(manifest["outputs"]["problems"]),
        "heuristics": Path(manifest["outputs"]["heuristics"]),
    }
    for key, path in output_paths.items():
        write_jsonl(path, by_kind[key.removesuffix("s")])
    canonical_input_hash = bundle_hash([clusters_path, logic_path, Path(manifest["outputs"]["evidence_markdown"])])
    projection_hashes = {key: sha256_file(path) for key, path in output_paths.items()}
    previous_hash = clean_text(manifest["stages"].get("canonical", {}).get("input_sha256"))
    report = {
        "valid": True,
        "stage": "canonical",
        "decision_count": len(clusters),
        "excluded_count": len(exclusions),
        "canonical_node_count": len(logic_nodes),
        "errors": [],
    }
    report_path = write_stage_report(survey_root, "canonical", report)
    now = utc_now()
    manifest["stages"]["canonical"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": canonical_input_hash,
        "output_sha256": sha256_file(logic_path),
        "report": str(report_path),
    }
    manifest["stages"]["projections"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": sha256_file(logic_path),
        "output_hashes": projection_hashes,
    }
    if previous_hash != canonical_input_hash:
        for stage in ("relations", "review", "query_pack"):
            manifest["stages"][stage] = {"status": downstream_status(manifest, stage), "updated_at": now}
    write_json(manifest_path, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def node_ref(value: str) -> str:
    linked = refs(value)
    if len(linked) == 1:
        return linked[0]
    paper_matches = [f"paper:{item}" for item in paper_ids(value)]
    return paper_matches[0] if len(paper_matches) == 1 else ""


def node_refs(value: str) -> list[str]:
    result = refs(value)
    result.extend(f"paper:{item}" for item in paper_ids(value))
    return list(dict.fromkeys(result))


def parse_relations_markdown(path: Path, survey_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return [], [{"code": "missing_file", "file": str(path), "message": "relations.md is missing"}]
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        actual = list(current["fields"])
        if actual != RELATION_FIELDS:
            errors.append({"code": "relation_field_schema_mismatch", "line": current["line"], "message": f"expected fields {RELATION_FIELDS}, got {actual}"})
        fields = current["fields"]
        source = node_ref(fields.get("From", ""))
        target = node_ref(fields.get("To", ""))
        source_packets = [item.strip() for item in fields.get("Source packets", "").split(",") if item.strip()]
        record = {
            "from": source,
            "to": target,
            "type": fields.get("Type", ""),
            "evidence": fields.get("Evidence", ""),
            "confidence": fields.get("Confidence", ""),
            "source_packets": source_packets,
            "source_nodes": node_refs(fields.get("Source nodes", "")),
            "evidence_level": fields.get("Evidence level", ""),
            "read_scope": fields.get("Read scope", ""),
            "source_block": relative_source_block(survey_root, path, current["block_id"]),
        }
        if not source or not target:
            errors.append({"code": "invalid_relation_endpoint", "line": current["line"], "message": "From and To must each contain exactly one node reference"})
        records.append(record)
        current = None

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        heading = RELATION_HEADING_RE.match(line)
        if heading:
            finish()
            number, title, slug = heading.groups()
            current = {"number": int(number), "title": title, "block_id": f"relation-{slug}", "line": line_number, "fields": {}}
            continue
        if current is not None:
            field = FIELD_RE.match(line)
            if field:
                current["fields"][field.group(1)] = field.group(2).strip()
            elif line.strip() and not line.startswith("## "):
                errors.append({"code": "unexpected_relation_content", "line": line_number, "message": "relation content must use one-line bold fields"})
    finish()
    return records, errors


def compile_relations(survey_root: Path, manifest_path: Path, manifest: dict[str, Any]) -> int:
    errors = verify_packet_inputs(manifest)
    for stage in ("canonical", "projections"):
        if manifest["stages"].get(stage, {}).get("status") != "valid":
            errors.append({"code": f"{stage}_not_valid", "message": f"{stage} stage must be valid"})
    logic_path = Path(manifest["outputs"]["logic_markdown"])
    if logic_path.exists() and sha256_file(logic_path) != manifest["stages"].get("canonical", {}).get("output_sha256"):
        errors.append({"code": "stale_canonical", "message": "logic.md changed after canonical validation"})
    projection_paths = {key: Path(manifest["outputs"][key]) for key in ("claims", "problems", "heuristics")}
    for key, expected in manifest["stages"].get("projections", {}).get("output_hashes", {}).items():
        path = projection_paths.get(key)
        if path is None or not path.exists() or sha256_file(path) != expected:
            errors.append({"code": "stale_projection", "message": f"{key} projection changed"})
    if errors:
        return fail_stage(survey_root, manifest_path, manifest, "relations", errors)

    relations_markdown = Path(manifest["outputs"]["relations_markdown"])
    records, parse_errors = parse_relations_markdown(relations_markdown, survey_root)
    if parse_errors:
        return fail_stage(survey_root, manifest_path, manifest, "relations", parse_errors)
    relations_path = Path(manifest["outputs"]["relations"])
    previous_signature = sha256_text(
        clean_text(manifest["stages"].get("relations", {}).get("input_sha256"))
        + clean_text(manifest["stages"].get("relations", {}).get("output_hashes", {}).get("relations"))
    )
    write_jsonl(relations_path, records)
    relation_report = research_relation_validate.validate(
        relations_path=relations_path,
        logic_dir=survey_root / "synthesis" / "logic",
        evidence_dir=survey_root / "synthesis" / "evidence",
        packet_index_path=Path(manifest["inputs"]["packet_index"]["path"]),
    )
    relation_report["stage"] = "relations"
    relation_report_path = survey_root / "synthesis" / "validation" / "relations.json"
    write_json(relation_report_path, relation_report)
    if not relation_report["valid"]:
        now = utc_now()
        manifest["stages"]["relations"] = {"status": "invalid", "updated_at": now, "report": str(relation_report_path)}
        for stage in ("review", "query_pack"):
            manifest["stages"][stage] = {"status": downstream_status(manifest, stage), "updated_at": now}
        write_json(manifest_path, manifest)
        print(json.dumps(relation_report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    now = utc_now()
    manifest["stages"]["relations"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": sha256_file(relations_markdown),
        "output_hashes": {
            "relations": sha256_file(relations_path),
            "validation": sha256_file(relation_report_path),
        },
        "report": str(relation_report_path),
    }
    current_signature = sha256_text(sha256_file(relations_markdown) + sha256_file(relations_path))
    if previous_signature != current_signature:
        for stage in ("review", "query_pack"):
            manifest["stages"][stage] = {"status": downstream_status(manifest, stage), "updated_at": now}
    write_json(manifest_path, manifest)
    print(json.dumps(relation_report, ensure_ascii=False, indent=2))
    return 0


def review_input_signature(manifest: dict[str, Any]) -> str:
    stages = manifest.get("stages", {})
    payload = {
        "evidence": stages.get("evidence", {}).get("output_hashes", {}),
        "canonical": stages.get("canonical", {}).get("input_sha256"),
        "projections": stages.get("projections", {}).get("output_hashes", {}),
        "relations": stages.get("relations", {}).get("output_hashes", {}).get("relations"),
    }
    return sha256_text(json.dumps(payload, sort_keys=True))


def normalize_review_cell(value: str) -> str:
    value = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = value.replace("**", "").replace("`", "")
    return " ".join(value.casefold().split())


def validate_review(path: Path, expected_papers: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return [{"code": "missing_literature_review", "message": f"literature review not found: {path}"}]
    text = path.read_text(encoding="utf-8").strip()
    errors: list[dict[str, Any]] = []
    header = "| Paper | Venue | Method | Key Result | Relevance to Us | Evidence Level | Source |"
    lines = text.splitlines()
    if header not in lines:
        errors.append({"code": "missing_review_table", "message": "literature review is missing the required paper table header"})
    else:
        header_index = lines.index(header)
        rows: list[list[str]] = []
        for line in lines[header_index + 2 :]:
            if not line.strip().startswith("|"):
                break
            if line.count("|") >= 7:
                rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        if not rows:
            errors.append({"code": "empty_review_table", "message": "literature review paper table has no data rows"})
        elif any(len(row) != 7 or any(not cell for cell in row) for row in rows):
            errors.append({"code": "invalid_review_row", "message": "every review-table row must contain seven non-empty cells"})
        if expected_papers is not None:
            expected_titles = {normalize_review_cell(clean_text(paper.get("title"))) for paper in expected_papers}
            actual_titles = {normalize_review_cell(row[0]) for row in rows if row}
            missing = sorted(title for title in expected_titles if title and title not in actual_titles)
            extra = sorted(title for title in actual_titles if title and title not in expected_titles)
            if missing:
                errors.append({"code": "review_papers_missing", "message": f"review table omits {len(missing)} deeply extracted papers"})
            if extra:
                errors.append({"code": "review_papers_unexpected", "message": f"review table includes {len(extra)} papers without deep extraction"})
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", "- ", "```")):
            continue
        if len(stripped) >= 40:
            paragraphs.append(stripped)
    if len(paragraphs) < 3:
        errors.append({"code": "insufficient_review_narrative", "message": "literature review needs at least three narrative paragraphs"})
    return errors


def compile_review(survey_root: Path, manifest_path: Path, manifest: dict[str, Any]) -> int:
    errors = audit_current(survey_root, manifest)
    review_path = Path(manifest["outputs"]["literature_review"])
    papers_path = Path(manifest["outputs"]["papers"])
    expected_papers = read_jsonl(papers_path) if papers_path.is_file() else []
    errors.extend(validate_review(review_path, expected_papers))
    if errors:
        return fail_stage(survey_root, manifest_path, manifest, "review", errors)
    input_signature = review_input_signature(manifest)
    output_hash = sha256_file(review_path)
    previous = manifest["stages"].get("review", {})
    changed = previous.get("input_sha256") != input_signature or previous.get("output_sha256") != output_hash
    report = {"valid": True, "stage": "review", "literature_review": str(review_path), "errors": []}
    report_path = write_stage_report(survey_root, "review", report)
    now = utc_now()
    manifest["stages"]["review"] = {
        "status": "valid",
        "updated_at": now,
        "input_sha256": input_signature,
        "output_sha256": output_hash,
        "report": str(report_path),
    }
    if changed:
        manifest["stages"]["query_pack"] = {"status": downstream_status(manifest, "query_pack"), "updated_at": now}
    write_json(manifest_path, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def audit_current(survey_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    errors = verify_packet_inputs(manifest)
    for stage in ("packets", "extraction", "evidence", "canonical", "projections", "relations"):
        if manifest["stages"].get(stage, {}).get("status") != "valid":
            errors.append({"code": "stage_not_valid", "stage": stage, "message": f"{stage} is not valid"})
    output_hash_paths = {
        "packets": {
            "papers": Path(manifest["outputs"]["papers"]),
        },
        "evidence": {
            "evidence_markdown": Path(manifest["outputs"]["evidence_markdown"]),
            "evidence_jsonl": Path(manifest["outputs"]["evidence"]),
            "candidate_index": Path(manifest["outputs"]["candidate_index"]),
        },
        "projections": {
            "claims": Path(manifest["outputs"]["claims"]),
            "problems": Path(manifest["outputs"]["problems"]),
            "heuristics": Path(manifest["outputs"]["heuristics"]),
        },
        "relations": {
            "relations": Path(manifest["outputs"]["relations"]),
            "validation": survey_root / "synthesis" / "validation" / "relations.json",
        },
    }
    for stage, paths in output_hash_paths.items():
        expected = manifest["stages"].get(stage, {}).get("output_hashes", {})
        for name, path in paths.items():
            if not path.exists() or expected.get(name) != sha256_file(path):
                errors.append({"code": "output_hash_mismatch", "stage": stage, "message": f"{name} is missing or stale"})
    extraction_paths = [Path(path) for path in manifest["outputs"]["extraction_files"]]
    if not all(path.exists() for path in extraction_paths):
        errors.append({"code": "missing_extraction", "stage": "extraction", "message": "one or more extraction files are missing"})
    elif manifest["stages"].get("extraction", {}).get("output_sha256") != bundle_hash(extraction_paths):
        errors.append({"code": "output_hash_mismatch", "stage": "extraction", "message": "extraction Markdown is stale"})
    canonical_paths = [
        Path(manifest["outputs"]["clusters"]),
        Path(manifest["outputs"]["logic_markdown"]),
        Path(manifest["outputs"]["evidence_markdown"]),
    ]
    if not all(path.exists() for path in canonical_paths):
        errors.append({"code": "missing_canonical_input", "stage": "canonical", "message": "canonical inputs are missing"})
    elif manifest["stages"].get("canonical", {}).get("input_sha256") != bundle_hash(canonical_paths):
        errors.append({"code": "input_hash_mismatch", "stage": "canonical", "message": "clusters, Logic, or Evidence changed after validation"})
    relations_markdown = Path(manifest["outputs"]["relations_markdown"])
    if not relations_markdown.exists():
        errors.append({"code": "missing_relations_markdown", "stage": "relations", "message": "relations.md is missing"})
    elif manifest["stages"].get("relations", {}).get("input_sha256") != sha256_file(relations_markdown):
        errors.append({"code": "input_hash_mismatch", "stage": "relations", "message": "relations.md changed after validation"})
    return errors


def audit_review_current(survey_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    errors = audit_current(survey_root, manifest)
    review_stage = manifest.get("stages", {}).get("review", {})
    review_path = Path(manifest.get("outputs", {}).get("literature_review", ""))
    if review_stage.get("status") != "valid":
        errors.append({"code": "stage_not_valid", "stage": "review", "message": "review is not valid"})
    if review_stage.get("input_sha256") != review_input_signature(manifest):
        errors.append({"code": "input_hash_mismatch", "stage": "review", "message": "synthesis changed after the review was validated"})
    if not review_path.is_file() or review_stage.get("output_sha256") != sha256_file(review_path):
        errors.append({"code": "output_hash_mismatch", "stage": "review", "message": "literature review is missing or stale"})
    return errors


def check_current(survey_root: Path, manifest: dict[str, Any]) -> int:
    errors = audit_current(survey_root, manifest)
    report = {"valid": not errors, "stage": "check", "errors": errors}
    write_stage_report(survey_root, "check", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing a current synthesis manifest.")
    parser.add_argument("stage", choices=("extraction", "canonical", "relations", "review", "check"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
        manifest_path, manifest = load_manifest(survey_root)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.stage == "extraction":
        return compile_extraction(survey_root, manifest_path, manifest)
    if args.stage == "canonical":
        return compile_canonical(survey_root, manifest_path, manifest)
    if args.stage == "relations":
        return compile_relations(survey_root, manifest_path, manifest)
    if args.stage == "review":
        return compile_review(survey_root, manifest_path, manifest)
    return check_current(survey_root, manifest)


if __name__ == "__main__":
    raise SystemExit(main())
