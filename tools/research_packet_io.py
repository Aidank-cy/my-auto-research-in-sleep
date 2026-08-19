#!/usr/bin/env python3
"""Read and write per-paper survey source packets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from research_artifact_io import sha256_file, write_json


PACKET_INDEX_SCHEMA_VERSION = 2
PACKET_INDEX_FILENAME = "index.json"


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def packet_filename(paper_id: str) -> str:
    normalized = clean_text(paper_id)
    if not normalized:
        raise ValueError("packet paper ID is empty")
    return f"{quote(normalized, safe='-._~')}.json"


def write_packet_bundle(
    output_dir: Path,
    *,
    index_metadata: dict[str, Any],
    packets: list[dict[str, Any]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for packet in packets:
        filename = packet_filename(clean_text(packet.get("id")))
        if filename in seen_files:
            raise ValueError(f"packet filename collision: {filename}")
        seen_files.add(filename)
        packet_path = output_dir / filename
        write_json(packet_path, packet)
        summary = {key: value for key, value in packet.items() if key != "text"}
        summary.update(packet_file=filename, packet_sha256=sha256_file(packet_path))
        summaries.append(summary)

    index = {
        **index_metadata,
        "schema_version": PACKET_INDEX_SCHEMA_VERSION,
        "packet_count": len(packets),
        "selected_paper_ids": [clean_text(packet.get("id")) for packet in packets],
        "packets": summaries,
    }
    index_path = output_dir / PACKET_INDEX_FILENAME
    write_json(index_path, index)
    return index_path


def load_packet_index(index_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("packet index must be a JSON object")
    if payload.get("schema_version") != PACKET_INDEX_SCHEMA_VERSION:
        raise ValueError(f"packet index schema_version must be {PACKET_INDEX_SCHEMA_VERSION}")
    records = payload.get("packets")
    if not isinstance(records, list):
        raise ValueError("packet index must contain a packets list")
    return payload, records


def load_packet(index_path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    filename = clean_text(summary.get("packet_file"))
    if not filename or Path(filename).name != filename or filename == PACKET_INDEX_FILENAME:
        raise ValueError(f"invalid packet_file for {clean_text(summary.get('id')) or '<unknown>'}: {filename!r}")
    packet_path = index_path.parent / filename
    if not packet_path.is_file():
        raise FileNotFoundError(f"packet file does not exist: {packet_path}")
    expected_hash = clean_text(summary.get("packet_sha256"))
    if not expected_hash or sha256_file(packet_path) != expected_hash:
        raise ValueError(f"packet file hash mismatch: {packet_path}")

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise ValueError(f"packet file must contain a JSON object: {packet_path}")
    if clean_text(packet.get("id")) != clean_text(summary.get("id")):
        raise ValueError(f"packet ID does not match index: {packet_path}")
    expected_summary = {key: value for key, value in packet.items() if key != "text"}
    actual_summary = {key: value for key, value in summary.items() if key not in {"packet_file", "packet_sha256"}}
    if actual_summary != expected_summary:
        raise ValueError(f"packet metadata does not match index: {packet_path}")
    return packet


def load_packet_bundle(index_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload, summaries = load_packet_index(index_path)
    packets = [load_packet(index_path, summary) for summary in summaries if isinstance(summary, dict)]
    if len(packets) != len(summaries):
        raise ValueError("every packet index entry must be a JSON object")
    return payload, packets


def load_packet_by_id(index_path: Path, paper_id: str) -> dict[str, Any] | None:
    _, summaries = load_packet_index(index_path)
    matches = [summary for summary in summaries if isinstance(summary, dict) and clean_text(summary.get("id")) == paper_id]
    if len(matches) > 1:
        raise ValueError(f"duplicate packet ID in index: {paper_id}")
    return load_packet(index_path, matches[0]) if matches else None
