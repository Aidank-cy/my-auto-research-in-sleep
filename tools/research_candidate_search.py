#!/usr/bin/env python3
"""Unified candidate-paper search for the research-lit skill.

This tool runs scriptable search sources, writes raw source outputs, deduplicates
papers, and emits candidate metadata for the research-lit pipeline.

Examples
--------
python3 tools/research_candidate_search.py \
  --query "agent memory" \
  --query "long context memory" \
  --topic-name agent-memory

python3 tools/research_candidate_search.py \
  --queries-json '["world models", "physical AI"]'
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_fetch  # noqa: E402
import huggingface_papers_fetch  # noqa: E402
import research_query_match as query_match  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root, survey_root_from_topic_name  # noqa: E402
import semantic_scholar_fetch  # noqa: E402
import verify_papers as paper_verifier  # noqa: E402

SOURCE_ORDER = ["database", "huggingface-papers", "arxiv", "semantic-scholar", "web-fallback"]
MAX_RELEVANCE_TEXT_CHARS = 3000
DEFAULT_REQUEST_SLEEP_SECONDS = 2.0
SEMANTIC_SCHOLAR_SEARCH_RETRIES = 15
SEMANTIC_SCHOLAR_SEARCH_RETRY_DELAY_SECONDS = 2.0

ARXIV_ID_RE = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


@dataclass
class SourceStatus:
    source: str
    attempted: bool = False
    succeeded: bool = False
    raw_count: int = 0
    usable_candidate_count: int = 0
    current_run_usable_candidate_count: int = 0
    historical_candidate_count: int = 0
    warning: str | None = None


class RequestPacer:
    """Enforce one minimum interval across every outbound search request."""

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds < 0:
            raise ValueError("request sleep must be >= 0")
        self.interval_seconds = interval_seconds
        self._last_request = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_request:
            remaining = self.interval_seconds - (now - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()


def configure_request_pacing(interval_seconds: float) -> RequestPacer:
    """Install one pacer in every network client used by this entrypoint."""
    pacer = RequestPacer(interval_seconds)
    huggingface_papers_fetch.set_request_hook(pacer.wait)
    arxiv_fetch.set_request_hook(pacer.wait)
    semantic_scholar_fetch.set_request_hook(pacer.wait)
    paper_verifier.set_request_hook(pacer.wait)
    return pacer


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    return text or None


def truncate_text(value: str, max_chars: int = MAX_RELEVANCE_TEXT_CHARS) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if "/abs/" in text:
        text = text.split("/abs/", 1)[1]
    if "/pdf/" in text:
        text = text.split("/pdf/", 1)[1].removesuffix(".pdf")
    text = text.removeprefix("ARXIV:").removeprefix("arxiv:").removeprefix("id:")
    match = ARXIV_ID_RE.search(text)
    if not match:
        return None
    return re.sub(r"v\d+$", "", match.group(0))


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    text = text.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    text = text.removeprefix("doi.org/")
    return text or None


def source_match_text(*parts: Any) -> str:
    values: list[str] = []
    for part in parts:
        if part in (None, "", [], {}):
            continue
        if isinstance(part, list):
            values.extend(str(item) for item in part if item not in (None, ""))
        else:
            values.append(str(part))
    return "\n".join(values)


def paper_key(candidate: dict[str, Any]) -> str | None:
    arxiv_id = normalize_arxiv_id(candidate.get("arxiv_id"))
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    doi = normalize_doi(candidate.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = normalize_title(candidate.get("title"))
    if title:
        return f"title:{title}"
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_skipped_source_artifact(search_dir: Path, source: str, queries: list[str]) -> None:
    paths = {
        "database": ("database_results.json", {"queries": queries, "papers": [], "skipped": True}),
        "huggingface-papers": ("huggingface_papers.json", {"queries": queries, "papers": [], "skipped": True}),
        "arxiv": ("arxiv_results.json", {"queries": queries, "results": [], "warnings": ["skipped"]}),
        "semantic-scholar": ("semantic_scholar_results.json", {"queries": queries, "results": [], "warnings": ["skipped"]}),
    }
    filename, payload = paths[source]
    write_json(search_dir / filename, payload)


def add_source(candidate: dict[str, Any], source: str) -> None:
    sources = candidate.setdefault("sources", [])
    if source not in sources:
        sources.append(source)


def merge_candidate(existing: dict[str, Any], incoming: dict[str, Any], source: str) -> None:
    add_source(existing, source)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        if key == "sources":
            for item in value:
                add_source(existing, str(item))
            continue
        if key == "source_payloads":
            existing.setdefault("source_payloads", {}).update(value)
            continue
        if key == "hf":
            existing.setdefault("hf", {}).update(value)
            continue
        if key == "semantic_scholar":
            existing.setdefault("semantic_scholar", {}).update(value)
            continue
        if key == "database":
            existing.setdefault("database", {}).update(value)
            continue
        if key not in existing or existing[key] in (None, "", [], {}):
            existing[key] = value
            continue
        if source == "arxiv" and key in {"title", "authors", "abstract", "published", "updated", "abs_url", "pdf_url"}:
            existing[key] = value
        if source == "semantic-scholar" and key in {"venue", "doi", "year", "citationCount", "publicationTypes", "publicationDate"}:
            existing[key] = value


def add_candidate(
    candidates: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    source: str,
) -> None:
    candidate["arxiv_id"] = normalize_arxiv_id(candidate.get("arxiv_id"))
    candidate["doi"] = normalize_doi(candidate.get("doi"))
    add_source(candidate, source)
    key = paper_key(candidate)
    if not key:
        return
    if key not in candidates:
        candidates[key] = candidate
        return
    merge_candidate(candidates[key], candidate, source)


def load_prior_candidates(search_dir: Path) -> list[dict[str, Any]]:
    path = search_dir / "candidate_metadata.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("candidates") if isinstance(payload, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def merge_prior_candidates(
    candidates: dict[str, dict[str, Any]],
    prior: list[dict[str, Any]],
) -> int:
    """Reuse same-topic records without inventing a new retrieval source."""
    reused = 0
    for old in prior:
        key = paper_key(old)
        if not key:
            continue
        if key not in candidates:
            candidates[key] = copy.deepcopy(old)
            reused += 1
            continue
        current = candidates[key]
        for field in ("id", "relevance", "local_pdf_path", "local_note_path"):
            if old.get(field) not in (None, "", [], {}) and current.get(field) in (None, "", [], {}):
                current[field] = copy.deepcopy(old[field])
        reused += 1
    return reused


def first_regex(pattern: str, text: str, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return clean_text(match.group(1))


def markdown_section(text: str, name: str, max_chars: int = 4000) -> str | None:
    matches = list(HEADING_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != name.lower():
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()[:max_chars] or None
    return None


def arxiv_id_from_text(text: str) -> str | None:
    return normalize_arxiv_id(first_regex(r"arxiv(?:\.org/abs/|:\s*)([^\s)\"']+)", text, re.I)) or normalize_arxiv_id(text)


def doi_from_text(text: str) -> str | None:
    match = DOI_RE.search(text)
    return normalize_doi(match.group(0)) if match else None


def database_note_candidate(path: Path, text: str, score: int) -> dict[str, Any]:
    title = first_regex(r"^#\s+(.+?)\s*$", text, re.M) or path.stem
    authors = first_regex(r"\*\*Authors:\*\*\s*(.+)", text)
    publication = first_regex(r"\*\*Publication:\*\*\s*(.+)", text)
    publication_date = first_regex(r"\*\*Publication Date:\*\*\s*(.+)", text)
    arxiv_id = arxiv_id_from_text(text)
    return {
        "title": title,
        "authors": [item.strip() for item in authors.split(";")] if authors and ";" in authors else authors,
        "year": publication_date[:4] if publication_date else None,
        "venue": publication,
        "arxiv_id": arxiv_id,
        "doi": doi_from_text(text),
        "abstract": markdown_section(text, "Abstract"),
        "local_note_path": str(path),
        "database": {"match_score": score, "kind": "wiki-note"},
        "source_payloads": {"database": {"path": str(path)}},
    }


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def iter_tsv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def database_metadata_candidate(path: Path, row: dict[str, Any], score: int) -> dict[str, Any]:
    title = clean_text(row.get("title") or row.get("display_name"))
    authors = row.get("authors")
    if isinstance(authors, str):
        authors = [item.strip() for item in re.split(r";|,", authors) if item.strip()]
    arxiv_id = normalize_arxiv_id(
        row.get("arxiv_id")
        or row.get("id")
        or row.get("pdf_url")
        or row.get("forum_url")
        or row.get("url")
    )
    return {
        "title": title,
        "authors": authors,
        "year": row.get("year") or row.get("publication_year"),
        "venue": row.get("venue"),
        "arxiv_id": arxiv_id,
        "doi": row.get("doi"),
        "abstract": clean_text(row.get("abstract")),
        "tldr": clean_text(row.get("tldr") or row.get("TLDR")),
        "pdf_url": clean_text(row.get("pdf_url")),
        "local_pdf_path": clean_text(row.get("pdf_path")),
        "database": {"match_score": score, "kind": "raw-metadata", "metadata_path": str(path)},
        "source_payloads": {"database": {"path": str(path), "row": row}},
    }


def run_database(queries: list[str], search_dir: Path, candidates: dict[str, dict[str, Any]]) -> SourceStatus:
    status = SourceStatus(source="database", attempted=True)
    database_root = REPO_ROOT / "database"
    if not database_root.exists():
        status.warning = "database directory not found"
        return status

    results: list[dict[str, Any]] = []
    try:
        for base in [database_root / "wiki" / "Papers & Blogs", database_root / "wiki" / "Entries"]:
            if not base.exists():
                continue
            for path in base.rglob("*.md"):
                text = path.read_text(encoding="utf-8", errors="replace")
                score = query_match.match_score(f"{path}\n{text}", queries)
                if score <= 0:
                    continue
                candidate = database_note_candidate(path, text, score)
                results.append(candidate)
                add_candidate(candidates, candidate, "database")

        for path in database_root.glob("raw/*/metadata/*.jsonl"):
            for row in iter_jsonl(path):
                score = query_match.match_score(json.dumps(row, ensure_ascii=False), queries)
                if score <= 0:
                    continue
                candidate = database_metadata_candidate(path, row, score)
                results.append(candidate)
                add_candidate(candidates, candidate, "database")

        for path in database_root.glob("raw/*/metadata/*.tsv"):
            for row in iter_tsv(path):
                score = query_match.match_score(json.dumps(row, ensure_ascii=False), queries)
                if score <= 0:
                    continue
                candidate = database_metadata_candidate(path, row, score)
                results.append(candidate)
                add_candidate(candidates, candidate, "database")

        status.succeeded = True
        status.raw_count = len(results)
        write_json(search_dir / "database_results.json", {"queries": queries, "papers": results})
    except Exception as exc:
        status.warning = str(exc)
    return status


def run_huggingface(
    queries: list[str],
    search_dir: Path,
    candidates: dict[str, dict[str, Any]],
    days: int,
    min_upvotes: int,
    refresh: bool,
) -> SourceStatus:
    status = SourceStatus(source="huggingface-papers", attempted=True)
    try:
        papers, stats = huggingface_papers_fetch.get_window(
            days,
            cache_dir=search_dir / "huggingface-papers-cache",
            refresh=refresh,
        )
        query_results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for query in queries:
            for result in huggingface_papers_fetch.search_papers(
                papers,
                min_upvotes=min_upvotes,
                query=query,
            ):
                item = huggingface_papers_fetch._result_to_dict(result)
                item["query"] = query
                query_results.append(item)
                paper_id = normalize_arxiv_id(item.get("id"))
                if not paper_id or paper_id in seen:
                    continue
                seen.add(paper_id)
                candidate = {
                    "title": item.get("title"),
                    "authors": item.get("authors"),
                    "arxiv_id": paper_id,
                    "published": item.get("publishedAt"),
                    "year": (item.get("publishedAt") or "")[:4] or None,
                    "abstract": None,
                    "abs_url": item.get("abs_url"),
                    "pdf_url": item.get("pdf_url"),
                    "hf": {
                        "upvotes": item.get("upvotes"),
                        "url": item.get("url"),
                        "summary": item.get("summary"),
                        "ai_summary": item.get("ai_summary"),
                        "ai_keywords": item.get("ai_keywords"),
                        "githubRepo": item.get("githubRepo"),
                        "projectPage": item.get("projectPage"),
                        "matched_queries": item.get("matched_queries"),
                        "match_score": item.get("match_score"),
                    },
                    "source_payloads": {"huggingface-papers": item},
                }
                add_candidate(candidates, candidate, "huggingface-papers")
        status.succeeded = True
        status.raw_count = len(query_results)
        write_json(
            search_dir / "huggingface_papers.json",
            {"stats": stats, "queries": queries, "papers": query_results},
        )
    except Exception as exc:
        status.warning = str(exc)
    return status


def run_arxiv(
    queries: list[str],
    search_dir: Path,
    candidates: dict[str, dict[str, Any]],
    max_results: int,
    year_from: int | None = None,
    year_to: int | None = None,
) -> SourceStatus:
    status = SourceStatus(source="arxiv", attempted=True)
    payload: list[dict[str, Any]] = []
    warnings: list[str] = []
    for query in queries:
        request_query = query_match.arxiv_query(query)
        if year_from is not None or year_to is not None:
            lower = year_from if year_from is not None else 1900
            upper = year_to if year_to is not None else 2999
            request_query = f"{request_query} AND submittedDate:[{lower}01010000 TO {upper}12312359]"
        try:
            results = arxiv_fetch.search(request_query, max_results=max_results)
        except Exception as exc:
            warnings.append(f"{query}: {exc}")
            continue
        payload.append({"query": query, "request_query": request_query, "papers": results})
        for item in results:
            score = query_match.match_score(
                source_match_text(
                    item.get("title"),
                    item.get("abstract"),
                    item.get("categories"),
                ),
                [query],
            )
            if score <= 0:
                continue
            candidate = {
                "title": item.get("title"),
                "authors": item.get("authors"),
                "arxiv_id": item.get("id"),
                "abstract": item.get("abstract"),
                "published": item.get("published"),
                "updated": item.get("updated"),
                "year": (item.get("published") or "")[:4] or None,
                "categories": item.get("categories"),
                "abs_url": item.get("abs_url"),
                "pdf_url": item.get("pdf_url"),
                "search": {"match_score": score, "matched_query": query},
                "source_payloads": {"arxiv": item},
            }
            add_candidate(candidates, candidate, "arxiv")
    status.raw_count = sum(len(item["papers"]) for item in payload)
    status.succeeded = bool(payload)
    status.warning = "; ".join(warnings) if warnings else None
    write_json(search_dir / "arxiv_results.json", {"queries": queries, "results": payload, "warnings": warnings})
    return status


def s2_author_names(authors: Any) -> list[str] | None:
    if not isinstance(authors, list):
        return None
    names = [clean_text(item.get("name")) for item in authors if isinstance(item, dict)]
    return [name for name in names if name]


def run_semantic_scholar(
    queries: list[str],
    search_dir: Path,
    candidates: dict[str, dict[str, Any]],
    max_results: int,
    year_from: int | None = None,
    year_to: int | None = None,
) -> SourceStatus:
    status = SourceStatus(source="semantic-scholar", attempted=True)
    payload: list[dict[str, Any]] = []
    warnings: list[str] = []
    old_key = os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    try:
        for query in queries:
            request_query = query_match.semantic_scholar_query(query)
            try:
                result = semantic_scholar_fetch.search(
                    request_query,
                    max_results=max_results,
                    retries=SEMANTIC_SCHOLAR_SEARCH_RETRIES,
                    retry_delay=SEMANTIC_SCHOLAR_SEARCH_RETRY_DELAY_SECONDS,
                    timeout=15,
                    year=(
                        f"{year_from or ''}-{year_to or ''}"
                        if year_from is not None or year_to is not None
                        else None
                    ),
                )
            except Exception as exc:
                warnings.append(f"{query}: {exc}")
                continue
            papers = result.get("data") or []
            payload.append({"query": query, "request_query": request_query, "response": result})
            for item in papers:
                external = item.get("externalIds") or {}
                doi = external.get("DOI") or external.get("doi")
                arxiv_id = external.get("ArXiv") or external.get("arxiv")
                open_pdf = item.get("openAccessPdf") or {}
                tldr = item.get("tldr")
                if isinstance(tldr, dict):
                    tldr = tldr.get("text")
                score = query_match.match_score(
                    source_match_text(
                        item.get("title"),
                        item.get("abstract"),
                        tldr,
                        item.get("fieldsOfStudy"),
                        item.get("s2FieldsOfStudy"),
                    ),
                    [query],
                )
                if score <= 0:
                    continue
                candidate = {
                    "title": item.get("title"),
                    "authors": s2_author_names(item.get("authors")),
                    "year": item.get("year"),
                    "venue": item.get("venue"),
                    "doi": doi,
                    "arxiv_id": arxiv_id,
                    "abstract": item.get("abstract"),
                    "tldr": tldr,
                    "url": item.get("url"),
                    "pdf_url": open_pdf.get("url") if isinstance(open_pdf, dict) else None,
                    "publicationTypes": item.get("publicationTypes"),
                    "publicationDate": item.get("publicationDate"),
                    "citationCount": item.get("citationCount"),
                    "search": {"match_score": score, "matched_query": query},
                    "semantic_scholar": {
                        "paperId": item.get("paperId"),
                        "externalIds": external,
                        "referenceCount": item.get("referenceCount"),
                        "fieldsOfStudy": item.get("fieldsOfStudy"),
                    },
                    "source_payloads": {"semantic-scholar": item},
                }
                add_candidate(candidates, candidate, "semantic-scholar")
    finally:
        if old_key is not None:
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = old_key

    status.raw_count = sum(len(item["response"].get("data") or []) for item in payload)
    status.succeeded = bool(payload)
    status.warning = "; ".join(warnings) if warnings else None
    write_json(
        search_dir / "semantic_scholar_results.json",
        {"queries": queries, "results": payload, "warnings": warnings},
    )
    return status


def assign_candidate_ids(candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    values = list(candidates.values())
    prior_ids = {
        candidate["id"]
        for candidate in values
        if re.fullmatch(r"p[1-9]\d*", clean_text(candidate.get("id")) or "")
    }
    if len(prior_ids) != sum(
        1 for candidate in values
        if re.fullmatch(r"p[1-9]\d*", clean_text(candidate.get("id")) or "")
    ):
        prior_ids = set()

    prior = sorted(
        (candidate for candidate in values if candidate.get("id") in prior_ids),
        key=lambda item: int(str(item["id"])[1:]),
    )
    new = sorted(
        (candidate for candidate in values if candidate.get("id") not in prior_ids),
        key=lambda item: (
            0 if "database" in item.get("sources", []) else 1,
            0 if item.get("arxiv_id") else 1,
            normalize_title(item.get("title")) or "",
        ),
    )
    next_id = max((int(value[1:]) for value in prior_ids), default=0) + 1
    for candidate in new:
        while f"p{next_id}" in prior_ids:
            next_id += 1
        candidate["id"] = f"p{next_id}"
        prior_ids.add(candidate["id"])
        next_id += 1
    return prior + new


def candidate_year(candidate: dict[str, Any]) -> int | None:
    for value in (
        candidate.get("year"),
        candidate.get("published"),
        candidate.get("publicationDate"),
    ):
        match = re.match(r"^(\d{4})", clean_text(value) or "")
        if match:
            return int(match.group(1))
    return None


def filter_year_range(
    candidates: dict[str, dict[str, Any]],
    year_from: int | None,
    year_to: int | None,
) -> dict[str, dict[str, Any]]:
    if year_from is None and year_to is None:
        return candidates
    filtered: dict[str, dict[str, Any]] = {}
    for key, candidate in candidates.items():
        year = candidate_year(candidate)
        if year is None:
            continue
        if year_from is not None and year < year_from:
            continue
        if year_to is not None and year > year_to:
            continue
        filtered[key] = candidate
    return filtered


def minimal_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "arxiv_id": candidate.get("arxiv_id"),
        "doi": candidate.get("doi"),
        "title": candidate.get("title"),
        "verification_status": candidate.get("verification_status"),
        "verification_method": candidate.get("verification_method"),
    }


def verify_candidates(
    candidates: list[dict[str, Any]],
    *,
    arxiv_batch_size: int,
    delay_seconds: float,
    fuzzy_threshold: float,
    cache_scope: str,
    cache_dir: str | None,
    cache_ttl_days: int,
    no_cache: bool,
    hallucination_warn_threshold: float,
) -> dict[str, Any]:
    """Verify candidates and attach only the public verification fields.

    Confidence and failure reasons belong to the separate audit receipt. They
    intentionally never become candidate fields.
    """
    papers = [
        paper_verifier.PaperInput(
            id=str(candidate["id"]),
            arxiv_id=clean_text(candidate.get("arxiv_id")),
            doi=clean_text(candidate.get("doi")),
            title=clean_text(candidate.get("title")),
        )
        for candidate in candidates
    ]
    cache: dict[str, dict[str, Any]] | None = None
    cache_path: Path | None = None
    if not no_cache and cache_scope != "none":
        cache_path = paper_verifier.resolve_cache_path(cache_scope, cache_dir)
        if cache_path:
            cache = paper_verifier.load_cache(cache_path, cache_ttl_days)

    results = paper_verifier.verify_papers(
        papers,
        arxiv_batch_size=arxiv_batch_size,
        delay_seconds=delay_seconds,
        fuzzy_threshold=fuzzy_threshold,
        user_email=os.environ.get("ARIS_VERIFY_EMAIL", "aris-research@anonymous.local").strip(),
        cache=cache,
    )
    if cache is not None and cache_path:
        paper_verifier.save_cache(cache_path, cache)

    by_id = {result.id: result for result in results}
    for candidate in candidates:
        result = by_id[str(candidate["id"])]
        candidate["verification_status"] = result.status
        candidate["verification_method"] = result.method
        candidate.pop("verification_confidence", None)
        candidate.pop("verification_reason", None)

    verdict, metrics = paper_verifier.compute_verdict(results, hallucination_warn_threshold)
    return {
        "verdict": verdict,
        **metrics,
        "candidate_count": len(candidates),
        "verified_count": sum(result.status == "verified" for result in results),
        "papers": [asdict(result) for result in results],
    }


def relevance_text(candidate: dict[str, Any]) -> str | None:
    title = clean_text(candidate.get("title"))
    prefix = f"Title: {title}\n" if title else ""

    abstract = clean_text(candidate.get("abstract"))
    if abstract:
        return truncate_text(f"{prefix}Abstract: {abstract}")

    tldr = clean_text(candidate.get("tldr"))
    if tldr:
        return truncate_text(f"{prefix}TLDR: {tldr}")

    hf = candidate.get("hf")
    if isinstance(hf, dict):
        ai_summary = clean_text(hf.get("ai_summary"))
        if ai_summary:
            return truncate_text(f"{prefix}Hugging Face AI summary: {ai_summary}")
        summary = clean_text(hf.get("summary"))
        if summary:
            return truncate_text(f"{prefix}Hugging Face summary: {summary}")

    if title:
        return truncate_text(f"Title: {title}")
    return None


def attach_relevance_text(candidate: dict[str, Any]) -> None:
    if clean_text(candidate.get("relevanceText")):
        return
    text = relevance_text(candidate)
    if text:
        candidate["relevanceText"] = text


def derive_topic_name(queries: list[str]) -> str:
    raw = queries[0].strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw).strip("-")
    return raw[:80] or "research-topic"


def default_survey_root(queries: list[str], *, repo_root: Path = REPO_ROOT) -> Path:
    return survey_root_from_topic_name(derive_topic_name(queries), repo_root=repo_root)


def parse_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    queries.extend(args.query or [])
    queries.extend(args.queries or [])
    if args.queries_json:
        payload = json.loads(args.queries_json)
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise ValueError("--queries-json must be a JSON list of strings")
        queries.extend(payload)
    if args.queries_file:
        path = Path(args.queries_file)
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("--queries-file JSON must contain a list")
            queries.extend(str(item) for item in payload)
        else:
            queries.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    cleaned = []
    seen = set()
    for query in queries:
        text = query.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        raise ValueError("provide at least one query via --query, --queries-json, --queries-file, or positional args")
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("queries", nargs="*", help="Query strings. Use quotes for multi-word queries.")
    parser.add_argument("--query", action="append", help="Query string; may be repeated.")
    parser.add_argument("--queries-json", help="JSON list of query strings.")
    parser.add_argument("--queries-file", help="Newline text file or JSON list of query strings.")
    add_survey_root_args(
        parser,
        survey_root_help="Survey root; default database/<first-query-slug>/survey.",
    )
    parser.add_argument("--max-arxiv", type=int, default=10)
    parser.add_argument("--max-s2", type=int, default=10)
    parser.add_argument("--hf-days", type=int, default=14)
    parser.add_argument("--hf-min-upvotes", type=int, default=50)
    parser.add_argument("--hf-refresh", action="store_true")
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--skip-database", action="store_true")
    parser.add_argument("--skip-hf", action="store_true")
    parser.add_argument("--skip-arxiv", action="store_true")
    parser.add_argument("--skip-s2", action="store_true")
    parser.add_argument(
        "--sleep",
        dest="sleep",
        type=float,
        default=DEFAULT_REQUEST_SLEEP_SECONDS,
        help=(
            "Minimum seconds between all outbound requests made by this command "
            "(default: 2.0)."
        ),
    )
    parser.add_argument("--verification-arxiv-batch-size", type=int, default=paper_verifier.DEFAULT_BATCH_SIZE)
    parser.add_argument("--verification-s2-fuzzy-threshold", type=float, default=paper_verifier.DEFAULT_FUZZY_THRESHOLD)
    parser.add_argument("--verification-cache-scope", choices=["project", "user", "none"], default="project")
    parser.add_argument("--verification-cache-dir")
    parser.add_argument("--verification-cache-ttl-days", type=int, default=paper_verifier.DEFAULT_CACHE_TTL_DAYS)
    parser.add_argument("--verification-no-cache", action="store_true")
    parser.add_argument(
        "--hallucination-warn-threshold",
        type=float,
        default=paper_verifier.DEFAULT_HALLUCINATION_WARN_THRESHOLD,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sleep < 0:
        print("Error: --sleep must be >= 0", file=sys.stderr)
        return 2
    if args.year_from is not None and args.year_to is not None and args.year_from > args.year_to:
        print("Error: --year-from must be <= --year-to", file=sys.stderr)
        return 2
    try:
        queries = parse_queries(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        survey_root = resolve_survey_root(
            args.survey_root,
            args.topic_name,
            repo_root=REPO_ROOT,
            default=default_survey_root(queries, repo_root=REPO_ROOT),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    search_dir = survey_root / "search"
    papers_dir = survey_root / "papers" / "downloaded"
    synthesis_dir = survey_root / "synthesis"
    for path in (search_dir, papers_dir, synthesis_dir):
        path.mkdir(parents=True, exist_ok=True)

    prior_candidates = load_prior_candidates(search_dir)

    configure_request_pacing(args.sleep)

    write_json(search_dir / "queries.json", queries)
    started = time.time()
    candidates: dict[str, dict[str, Any]] = {}

    statuses = {source: SourceStatus(source=source) for source in SOURCE_ORDER}
    if not args.skip_database:
        statuses["database"] = run_database(queries, search_dir, candidates)
    else:
        write_skipped_source_artifact(search_dir, "database", queries)
    if not args.skip_hf:
        statuses["huggingface-papers"] = run_huggingface(
            queries,
            search_dir,
            candidates,
            days=args.hf_days,
            min_upvotes=args.hf_min_upvotes,
            refresh=args.hf_refresh,
        )
    else:
        write_skipped_source_artifact(search_dir, "huggingface-papers", queries)
    if not args.skip_arxiv:
        statuses["arxiv"] = run_arxiv(
            queries, search_dir, candidates, max_results=args.max_arxiv,
            year_from=args.year_from, year_to=args.year_to,
        )
    else:
        write_skipped_source_artifact(search_dir, "arxiv", queries)
    if not args.skip_s2:
        statuses["semantic-scholar"] = run_semantic_scholar(
            queries,
            search_dir,
            candidates,
            max_results=args.max_s2,
            year_from=args.year_from,
            year_to=args.year_to,
        )
    else:
        write_skipped_source_artifact(search_dir, "semantic-scholar", queries)
    statuses["web-fallback"] = SourceStatus(
        source="web-fallback",
        attempted=False,
        succeeded=False,
        warning="not handled by local candidate search tool; use Codex browsing only if needed",
    )

    current_source_keys = {
        source: {
            key for key, candidate in candidates.items()
            if source in candidate.get("sources", [])
        }
        for source in SOURCE_ORDER
    }
    reused_prior_count = merge_prior_candidates(candidates, prior_candidates)

    candidates = filter_year_range(candidates, args.year_from, args.year_to)
    candidate_list = assign_candidate_ids(candidates)
    for candidate in candidate_list:
        key = paper_key(candidate)
        candidate["current_run_sources"] = [
            source for source in SOURCE_ORDER if key in current_source_keys[source]
        ]
        attach_relevance_text(candidate)
    verification = verify_candidates(
        candidate_list,
        arxiv_batch_size=args.verification_arxiv_batch_size,
        # The shared request hook already paces every verifier HTTP call.
        delay_seconds=0,
        fuzzy_threshold=args.verification_s2_fuzzy_threshold,
        cache_scope=args.verification_cache_scope,
        cache_dir=args.verification_cache_dir,
        cache_ttl_days=args.verification_cache_ttl_days,
        no_cache=args.verification_no_cache,
        hallucination_warn_threshold=args.hallucination_warn_threshold,
    )
    for status in statuses.values():
        current_count = (
            sum(1 for candidate in candidate_list if status.source in candidate["current_run_sources"])
            if status.succeeded
            else 0
        )
        status.usable_candidate_count = current_count
        status.current_run_usable_candidate_count = current_count
        status.historical_candidate_count = sum(
            1 for candidate in candidate_list if status.source in (candidate.get("sources") or [])
        )

    source_status = [asdict(statuses[source]) for source in SOURCE_ORDER]
    verified_candidates = [
        candidate for candidate in candidate_list
        if candidate.get("verification_status") == "verified"
    ]
    candidate_papers = [minimal_candidate(candidate) for candidate in verified_candidates]
    metadata = {
        "queries": queries,
        "survey_root": str(survey_root),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": candidate_list,
    }
    summary = {
        "survey_root": str(survey_root),
        "query_count": len(queries),
        "year_from": args.year_from,
        "year_to": args.year_to,
        "request_sleep_seconds": args.sleep,
        "candidate_count": len(candidate_list),
        "verified_candidate_count": len(candidate_papers),
        "reused_prior_candidate_count": reused_prior_count,
        "source_status": source_status,
        "elapsed_seconds": round(time.time() - started, 2),
        "no_scriptable_evidence": not any(
            status.usable_candidate_count > 0
            for name, status in statuses.items()
            if name != "web-fallback"
        ),
        "no_verified_evidence": bool(candidate_list) and not candidate_papers,
        "verification_verdict": verification["verdict"],
    }

    write_json(search_dir / "source_status.json", source_status)
    write_json(search_dir / "verification_status.json", verification)
    write_json(search_dir / "candidate_papers.json", candidate_papers)
    write_json(search_dir / "candidate_metadata.json", metadata)
    write_json(search_dir / "search_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["no_scriptable_evidence"]:
        return 2
    if summary["no_verified_evidence"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
