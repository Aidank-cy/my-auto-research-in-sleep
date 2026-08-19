#!/usr/bin/env python3
"""Validate research-lit relation edges against survey-local nodes and packets."""

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

from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402
from research_artifact_io import write_json  # noqa: E402
from research_packet_io import load_packet_bundle  # noqa: E402


VALID_RELATION_TYPES = {
    "extends",
    "contradicts",
    "addresses_gap",
    "inspired_by",
    "tested_by",
    "supports",
    "invalidates",
    "supersedes",
}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_EVIDENCE_LEVELS = {"metadata-only", "local-note", "fulltext", "mixed"}
METADATA_ONLY_ALIASES = {"metadata-only", "metadata_only", "metadata"}
EVIDENCE_LEVEL_ALIASES = {
    "metadata_only": "metadata-only",
    "metadata": "metadata-only",
    "local_note": "local-note",
    "full-text": "fulltext",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_evidence_level(value: Any) -> str:
    text = clean_text(value).lower()
    return EVIDENCE_LEVEL_ALIASES.get(text, text)


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not path.exists():
        return records, [{"line": None, "code": "missing_file", "message": f"{path} does not exist"}]
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "line": line_number,
                    "code": "invalid_json",
                    "message": f"invalid JSON: {exc}",
                }
            )
            continue
        if not isinstance(record, dict):
            errors.append(
                {
                    "line": line_number,
                    "code": "not_object",
                    "message": "relation line must be a JSON object",
                }
            )
            continue
        records.append({"__line__": line_number, **record})
    return records, errors


def load_nodes(nodes_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    if not nodes_dir.exists():
        return nodes, [{"line": None, "code": "missing_nodes_dir", "message": f"{nodes_dir} does not exist"}]
    for path in sorted(nodes_dir.glob("*.jsonl")):
        records, path_errors = read_jsonl(path)
        for error in path_errors:
            errors.append({**error, "file": str(path)})
        for record in records:
            node_id = clean_text(record.get("node_id"))
            if node_id:
                if node_id in nodes:
                    errors.append(
                        {
                            "line": record.get("__line__"),
                            "code": "duplicate_node_id",
                            "file": str(path),
                            "message": f"duplicate node ID: {node_id}",
                        }
                    )
                    continue
                nodes[node_id] = {key: value for key, value in record.items() if key != "__line__"}
    return nodes, errors


def load_packets(path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return {}, [{"line": None, "code": "missing_packet_index", "message": f"{path} does not exist"}]
    try:
        _, records = load_packet_bundle(path)
    except Exception as exc:
        return {}, [{"line": None, "code": "invalid_packet_index", "message": str(exc)}]
    packets: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append({"line": None, "code": "invalid_packet", "message": f"packet at index {index} is not an object"})
            continue
        packet_id = clean_text(record.get("id"))
        if not packet_id:
            errors.append({"line": None, "code": "missing_packet_id", "message": f"packet at index {index} is missing id"})
        elif packet_id in packets:
            errors.append({"line": None, "code": "duplicate_packet_id", "message": f"duplicate packet ID: {packet_id}"})
        else:
            packets[packet_id] = record
    return packets, errors


def as_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        text = clean_text(item)
        if text:
            result.append(text)
    return result


def add_error(errors: list[dict[str, Any]], relation: dict[str, Any], code: str, message: str) -> None:
    errors.append(
        {
            "line": relation.get("__line__"),
            "code": code,
            "message": message,
            "from": relation.get("from"),
            "to": relation.get("to"),
            "type": relation.get("type"),
        }
    )


def node_kind(node_id: str) -> str:
    return node_id.split(":", 1)[0] if ":" in node_id else ""


def node_exists(node_id: str, nodes: dict[str, dict[str, Any]]) -> bool:
    """Treat idea/experiment IDs as externally owned until their schemas land."""
    return node_kind(node_id) in {"idea", "exp"} or node_id in nodes


def endpoints_allowed(relation_type: str, source: str, target: str) -> bool:
    pair = (node_kind(source), node_kind(target))
    if relation_type == "extends":
        return pair in {("paper", "paper"), ("claim", "claim")}
    if relation_type == "contradicts":
        return pair in {("paper", "paper"), ("evidence", "evidence"), ("claim", "claim")}
    if relation_type == "addresses_gap":
        return pair[0] in {"paper", "claim", "heuristic"} and pair[1] == "problem"
    if relation_type == "inspired_by":
        return pair[0] in {"claim", "problem", "heuristic"} and pair[1] == "paper"
    if relation_type == "supersedes":
        return pair == ("paper", "paper")
    if relation_type == "tested_by":
        return pair[0] in {"claim", "idea"} and pair[1] == "exp"
    if relation_type in {"supports", "invalidates"}:
        return pair[0] == "exp" and pair[1] in {"claim", "idea"}
    return False


def validate_relation(
    relation: dict[str, Any],
    *,
    nodes: dict[str, dict[str, Any]],
    packets: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    source = clean_text(relation.get("from"))
    target = clean_text(relation.get("to"))
    relation_type = clean_text(relation.get("type"))
    evidence = clean_text(relation.get("evidence"))
    confidence = clean_text(relation.get("confidence")).lower()
    evidence_level = normalize_evidence_level(relation.get("evidence_level"))
    read_scope = clean_text(relation.get("read_scope"))
    source_packets = as_string_list(relation.get("source_packets"))
    source_nodes = as_string_list(relation.get("source_nodes"))

    if not source:
        add_error(errors, relation, "missing_from", "relation is missing from")
    elif not node_exists(source, nodes):
        add_error(errors, relation, "unknown_from", f"from node does not exist: {source}")

    if not target:
        add_error(errors, relation, "missing_to", "relation is missing to")
    elif not node_exists(target, nodes):
        add_error(errors, relation, "unknown_to", f"to node does not exist: {target}")

    if relation_type not in VALID_RELATION_TYPES:
        add_error(errors, relation, "invalid_type", f"invalid relation type: {relation_type}")
    elif source and target and not endpoints_allowed(relation_type, source, target):
        add_error(errors, relation, "invalid_endpoints", f"{relation_type} does not allow {node_kind(source)} -> {node_kind(target)}")

    if not evidence:
        add_error(errors, relation, "missing_evidence", "relation evidence must be non-empty")

    if confidence not in VALID_CONFIDENCE:
        add_error(errors, relation, "invalid_confidence", f"confidence must be one of {sorted(VALID_CONFIDENCE)}")

    if evidence_level not in VALID_EVIDENCE_LEVELS:
        add_error(errors, relation, "invalid_evidence_level", f"evidence_level must be one of {sorted(VALID_EVIDENCE_LEVELS)}")

    if not read_scope:
        add_error(errors, relation, "missing_read_scope", "read_scope must be non-empty")

    if source_packets is None or not source_packets:
        add_error(errors, relation, "missing_source_packets", "source_packets must be a non-empty list")
        source_packets = []

    if source_nodes is None or not source_nodes:
        add_error(errors, relation, "missing_source_nodes", "source_nodes must be a non-empty list")
        source_nodes = []

    packet_levels: list[str] = []
    for packet_id in source_packets:
        packet = packets.get(packet_id)
        if packet is None:
            add_error(errors, relation, "unknown_source_packet", f"source packet does not exist: {packet_id}")
            continue
        packet_levels.append(normalize_evidence_level(packet.get("evidence_level")))
        if not re.search(rf"(?:^|;\s*){re.escape(packet_id)}:", read_scope):
            add_error(errors, relation, "missing_packet_scope", f"read_scope is missing packet locator: {packet_id}")

    if packet_levels:
        invalid_packet_levels = sorted({level for level in packet_levels if level not in VALID_EVIDENCE_LEVELS - {"mixed"}})
        if invalid_packet_levels:
            add_error(errors, relation, "invalid_packet_evidence_level", f"source packets have invalid evidence levels: {invalid_packet_levels}")
        expected_level = packet_levels[0] if len(set(packet_levels)) == 1 else "mixed"
        if evidence_level != expected_level:
            add_error(errors, relation, "evidence_level_mismatch", f"evidence_level must be {expected_level} for the listed packets")

    for node_id in source_nodes:
        if not node_exists(node_id, nodes):
            add_error(errors, relation, "unknown_source_node", f"source node does not exist: {node_id}")

    relation_metadata_only = evidence_level in METADATA_ONLY_ALIASES
    packets_metadata_only = bool(packet_levels) and all(level in METADATA_ONLY_ALIASES for level in packet_levels)
    if confidence == "high" and (relation_metadata_only or packets_metadata_only):
        add_error(
            errors,
            relation,
            "metadata_only_high_confidence",
            "metadata-only provenance cannot use high confidence",
        )


def validate(
    *,
    relations_path: Path,
    logic_dir: Path,
    evidence_dir: Path,
    packet_index_path: Path,
) -> dict[str, Any]:
    logic_nodes, logic_node_errors = load_nodes(logic_dir)
    evidence_nodes, evidence_node_errors = load_nodes(evidence_dir)
    nodes = {**evidence_nodes, **logic_nodes}
    packets, packet_errors = load_packets(packet_index_path)
    relations, relation_parse_errors = read_jsonl(relations_path)
    errors = [*logic_node_errors, *evidence_node_errors, *packet_errors, *relation_parse_errors]

    for relation in relations:
        validate_relation(relation, nodes=nodes, packets=packets, errors=errors)

    return {
        "valid": not errors,
        "relations": str(relations_path),
        "logic_dir": str(logic_dir),
        "evidence_dir": str(evidence_dir),
        "packet_index": str(packet_index_path),
        "node_count": len(nodes),
        "packet_count": len(packets),
        "edge_count": len(relations),
        "valid_relation_types": sorted(VALID_RELATION_TYPES),
        "valid_confidence": sorted(VALID_CONFIDENCE),
        "valid_evidence_levels": sorted(VALID_EVIDENCE_LEVELS),
        "externally_owned_node_prefixes": ["idea", "exp"],
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_survey_root_args(parser, survey_root_help="Survey root containing synthesis/logic/relations.jsonl.")
    parser.add_argument("--relations", type=Path, help="Default: <survey-root>/synthesis/logic/relations.jsonl")
    parser.add_argument("--logic-dir", type=Path, help="Default: <survey-root>/synthesis/logic")
    parser.add_argument("--evidence-dir", type=Path, help="Default: <survey-root>/synthesis/evidence")
    parser.add_argument(
        "--packet-index",
        type=Path,
        help="Default: <survey-root>/synthesis/packets/index.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Default: <survey-root>/synthesis/validation/relations.json",
    )
    parser.add_argument("--no-report", action="store_true", help="Do not write a JSON validation report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    logic_dir = args.logic_dir or (survey_root / "synthesis" / "logic")
    evidence_dir = args.evidence_dir or (survey_root / "synthesis" / "evidence")
    relations_path = args.relations or (logic_dir / "relations.jsonl")
    packet_index_path = args.packet_index or (survey_root / "synthesis" / "packets" / "index.json")
    report_path = args.report or (survey_root / "synthesis" / "validation" / "relations.json")

    report = validate(
        relations_path=relations_path,
        logic_dir=logic_dir,
        evidence_dir=evidence_dir,
        packet_index_path=packet_index_path,
    )
    if not args.no_report:
        write_json(report_path, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
