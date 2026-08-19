#!/usr/bin/env python3
"""Shared survey path helpers for research-lit tools."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def add_survey_root_args(
    parser,
    *,
    survey_root_help: str = "Survey root directory.",
    topic_name_help: str = "Survey topic name; resolves to database/<topic-name>/survey.",
) -> None:
    parser.add_argument("--topic-name", help=topic_name_help)
    parser.add_argument("--survey-root", type=Path, help=survey_root_help)


def survey_root_from_topic_name(topic_name: str, *, repo_root: Path = REPO_ROOT) -> Path:
    topic = topic_name.strip()
    if not topic:
        raise ValueError("--topic-name must not be empty")
    if "/" in topic or "\\" in topic:
        raise ValueError("--topic-name must be a single directory name, not a path")
    if topic in {".", ".."}:
        raise ValueError("--topic-name must not be '.' or '..'")
    return repo_root / "database" / topic / "survey"


def resolve_survey_root(
    survey_root: Path | None,
    topic_name: str | None,
    *,
    repo_root: Path = REPO_ROOT,
    default: Path | None = None,
    required: bool = True,
) -> Path | None:
    if survey_root is not None and topic_name is not None:
        raise ValueError("provide --topic-name or --survey-root, not both")
    if topic_name is not None:
        return survey_root_from_topic_name(topic_name, repo_root=repo_root)
    if survey_root is not None:
        return survey_root
    if default is not None:
        return default
    if required:
        raise ValueError("provide --topic-name or --survey-root")
    return None
