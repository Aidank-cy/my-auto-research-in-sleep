#!/usr/bin/env python3
"""CLI helper for fetching Semantic Scholar papers.

Designed to complement arxiv_fetch.py: arXiv handles preprints, this tool
handles **published venue papers** (IEEE, ACM, Springer, etc.) with rich
metadata (citations, venue, fieldsOfStudy, TLDR).

Commands
--------
search       Relevance search for papers (offset pagination, max 100).
search-bulk  Bulk search with token-based pagination (max 1000).
paper        Fetch one paper by Semantic Scholar paper ID, DOI, CorpusId, ArXiv ID, etc.
references   List the papers a given paper cites (its own forward reference list).
             Retained for standalone and compatibility reference workflows;
             current research-lit candidate citation enrichment uses ``paper``.

Filter flags (shared by search and search-bulk)
-----------------------------------------------
--fields-of-study   e.g. "Computer Science,Engineering"
--publication-types  e.g. "JournalArticle", "Conference", "Review"
--min-citations      e.g. 10
--year               e.g. "2020-", "2020-2024"
--venue              exact venue name, e.g. "IEEE Transactions on Signal Processing"
--open-access        only papers with a public PDF

Examples
--------
# Search for journal articles with >= 10 citations (best combo for quality filtering)
python3 tools/semantic_scholar_fetch.py search "semantic communication" --max 10 \
  --publication-types JournalArticle --min-citations 10

# CS/Engineering papers from 2022 onward
python3 tools/semantic_scholar_fetch.py search "semantic communication" --max 10 \
  --fields-of-study "Computer Science,Engineering" --year "2022-"

# Bulk search sorted by citation count, CS only
python3 tools/semantic_scholar_fetch.py search-bulk "semantic communication" --max 50 \
  --sort citationCount:desc --fields-of-study "Computer Science" --year "2020-"

# Fetch a single paper by DOI or arXiv ID
python3 tools/semantic_scholar_fetch.py paper "10.1109/JSAC.2021.3126077"
python3 tools/semantic_scholar_fetch.py paper "ARXIV:2006.10685"

# List what a paper cites (its own reference list) for standalone/compatibility workflows.
python3 tools/semantic_scholar_fetch.py references "ARXIV:2006.10685" --limit 500

# NOTE: --venue requires exact venue name (e.g. "IEEE Transactions on Signal Processing"),
# not partial match like "IEEE". Prefer --publication-types + --fields-of-study instead.
"""

from __future__ import annotations

import argparse
import email.utils
import fcntl
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context backed by certifi's CA bundle when available.

    See arxiv_fetch.py's copy of this helper for the full rationale — this
    fixes CERTIFICATE_VERIFY_FAILED on a python.org macOS install whose
    "Install Certificates.command" step was never run. Returns None (urllib's
    own default) if certifi isn't installed.
    """
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ, without
    overriding variables already set in the real environment. No external
    dependency (python-dotenv) required — this project deliberately stays
    stdlib-only (urllib, not requests; argparse, not click).

    Looks for .env at the current working directory first (the repo root,
    per this project's canonical `cd "$(git rev-parse --show-toplevel ...)"`
    bash preamble used before every tool invocation), then falls back to
    this script's own tools/ parent directory, for robustness when invoked
    directly without that preamble.
    """
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for candidate in candidates:
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


_load_dotenv()

_API_BASE = "https://api.semanticscholar.org/graph/v1"
_USER_AGENT = "s2-fetch/1.1"
_DEFAULT_TIMEOUT = 30
_DEFAULT_RETRIES = int(os.getenv("S2_FETCH_RETRIES", "4"))
_DEFAULT_RETRY_DELAY = float(os.getenv("S2_FETCH_RETRY_DELAY", "2.0"))
_UNAUTHENTICATED_MIN_INTERVAL = float(os.getenv("S2_UNAUTHENTICATED_MIN_INTERVAL", "2.0"))
_AUTHENTICATED_MIN_INTERVAL = float(os.getenv("S2_AUTHENTICATED_MIN_INTERVAL", "1.1"))
_HTTP_429_COOLDOWN_FLOOR = float(os.getenv("S2_429_COOLDOWN_FLOOR", str(_DEFAULT_RETRY_DELAY)))
_SEARCH_PAGE_SIZE = 100
_BULK_PAGE_SIZE = 1000
_REQUEST_HOOK: Callable[[], None] | None = None


def set_request_hook(hook: Callable[[], None] | None) -> None:
    """Set an optional caller-owned hook that runs before every HTTP attempt."""
    global _REQUEST_HOOK
    _REQUEST_HOOK = hook


def _run_request_hook() -> None:
    if _REQUEST_HOOK is not None:
        _REQUEST_HOOK()

# Good default for relevance search / single-paper fetch
_DEFAULT_FIELDS = (
    "paperId,title,abstract,year,venue,publicationVenue,publicationTypes,"
    "publicationDate,url,openAccessPdf,authors,externalIds,citationCount,"
    "referenceCount,fieldsOfStudy,s2FieldsOfStudy"
)

# Bulk search is intended for basic paper data; keep defaults conservative
_DEFAULT_BULK_FIELDS = (
    "paperId,title,abstract,year,venue,publicationDate,url,authors,"
    "externalIds,citationCount,referenceCount,fieldsOfStudy"
)


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _rate_limit_state_path() -> Path:
    """Return the shared, local request-scheduling state path.

    Candidate search, citation enrichment, and standalone reference tools may
    invoke this module in separate processes. A per-process sleep cannot prevent those processes
    from bursting together, so state lives under the ignored `.aris/` run
    area. Tests may override the location without mutating project state.
    """
    override = os.getenv("S2_RATE_LIMIT_STATE_PATH", "").strip()
    if override:
        return Path(override)
    return Path.cwd() / ".aris" / "semantic-scholar-rate-limit.json"


def _minimum_interval() -> float:
    return _AUTHENTICATED_MIN_INTERVAL if os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip() else _UNAUTHENTICATED_MIN_INTERVAL


def _with_locked_rate_state(update: Any) -> Any:
    path = _rate_limit_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            try:
                state = json.load(handle)
            except (json.JSONDecodeError, ValueError):
                state = {}
            result = update(state)
            handle.seek(0)
            handle.truncate()
            json.dump(state, handle)
            handle.flush()
            return result
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reserve_request_slot() -> None:
    """Serialize all local S2 requests at the configured safe interval."""
    now = time.time()

    def reserve(state: dict[str, Any]) -> float:
        not_before = float(state.get("not_before", 0.0))
        request_at = max(now, not_before)
        state["not_before"] = request_at + _minimum_interval()
        return max(0.0, request_at - now)

    wait_seconds = _with_locked_rate_state(reserve)
    if wait_seconds:
        time.sleep(wait_seconds)


def _extend_global_cooldown(delay: float) -> None:
    """Make all later local S2 calls wait after a server throttle response."""
    not_before = time.time() + max(0.0, delay)

    def extend(state: dict[str, Any]) -> None:
        state["not_before"] = max(float(state.get("not_before", 0.0)), not_before)

    _with_locked_rate_state(extend)


def _retry_after_seconds(value: str | None, fallback: float) -> float:
    """Parse Retry-After as delay-seconds or an HTTP date."""
    if not value:
        return fallback
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError, OverflowError):
            return fallback
        return max(0.0, retry_at - time.time())


def _request_json(
    url: str,
    *,
    retries: int = _DEFAULT_RETRIES,
    timeout: int = _DEFAULT_TIMEOUT,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> dict[str, Any]:
    """Fetch and parse a JSON response, retrying on 429/5xx and network errors.

    Unauthenticated Semantic Scholar traffic (no SEMANTIC_SCHOLAR_API_KEY) is
    rate-limited aggressively and shared across every unauthenticated caller
    worldwide, so 429s are routine, not exceptional, especially when a
    caller loops this function once per paper (for example current citation
    enrichment or a standalone reference fetch). `retries=4` with a
    fixed retry delay and a shared local rate gate. `Retry-After` is used when
    supplied; otherwise a retry waits the configured retry delay. The gate
    covers separate search/reference CLI processes, not only retries within
    this one process.
    """
    req = urllib.request.Request(url, headers=_headers())
    last_err: Exception | None = None

    for attempt in range(retries + 1):
        try:
            _reserve_request_slot()
            _run_request_hook()
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            retryable = exc.code in (429, 500, 502, 503, 504)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = _retry_after_seconds(retry_after, retry_delay)

            # A terminal 429 still means the shared public pool is throttled.
            # Publish that cooldown before raising so the next process does not
            # immediately consume another doomed request slot. The ordinary
            # two-second gate and a server-throttle cooldown are deliberately
            # separate controls.
            if exc.code == 429:
                _extend_global_cooldown(max(delay, _HTTP_429_COOLDOWN_FLOOR))

            if retryable and attempt < retries:
                if exc.code != 429:
                    time.sleep(delay)
                last_err = exc
                continue

            message = f"HTTP {exc.code}"
            if body:
                message += f": {body}"
            if exc.code == 429 and not os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip():
                message += " (no SEMANTIC_SCHOLAR_API_KEY is configured; add one in .env for an authenticated quota)"
            raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(retry_delay)
                last_err = exc
                continue
            raise RuntimeError(f"Network error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse JSON response from Semantic Scholar API") from exc

    raise RuntimeError(f"Request failed after retries: {last_err}")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    return text or None


def _parse_author(author: dict[str, Any]) -> dict[str, Any]:
    return {
        "authorId": author.get("authorId"),
        "name": _clean_text(author.get("name")),
    }


def _parse_publication_venue(pub_venue: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pub_venue:
        return None
    return {
        "id": pub_venue.get("id"),
        "name": _clean_text(pub_venue.get("name")),
        "type": _clean_text(pub_venue.get("type")),
        "issn": _clean_text(pub_venue.get("issn")),
        "url": _clean_text(pub_venue.get("url")),
    }


def _parse_paper(paper: dict[str, Any]) -> dict[str, Any]:
    authors = paper.get("authors") or []
    return {
        "paperId": paper.get("paperId"),
        "title": _clean_text(paper.get("title")),
        "abstract": _clean_text(paper.get("abstract")),
        "year": paper.get("year"),
        "venue": _clean_text(paper.get("venue")),
        "publicationVenue": _parse_publication_venue(paper.get("publicationVenue")),
        "publicationTypes": paper.get("publicationTypes"),
        "publicationDate": _clean_text(paper.get("publicationDate")),
        "url": _clean_text(paper.get("url")),
        "openAccessPdf": paper.get("openAccessPdf"),
        "authors": [_parse_author(a) for a in authors],
        "externalIds": paper.get("externalIds"),
        "citationCount": paper.get("citationCount"),
        "referenceCount": paper.get("referenceCount"),
        "fieldsOfStudy": paper.get("fieldsOfStudy"),
        "s2FieldsOfStudy": paper.get("s2FieldsOfStudy"),
        "tldr": paper.get("tldr"),
    }


def search(
    query: str,
    max_results: int = 10,
    offset: int = 0,
    fields: str = _DEFAULT_FIELDS,
    fields_of_study: str | None = None,
    venue: str | None = None,
    year: str | None = None,
    min_citation_count: int | None = None,
    publication_types: str | None = None,
    open_access_pdf: bool = False,
    retries: int | None = None,
    retry_delay: float | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    if max_results <= 0:
        return {"mode": "search", "total": 0, "offset": offset, "next_offset": offset, "data": []}

    common_params: dict[str, Any] = {"query": query, "fields": fields}
    if fields_of_study:
        common_params["fieldsOfStudy"] = fields_of_study
    if venue:
        common_params["venue"] = venue
    if year:
        common_params["year"] = year
    if min_citation_count is not None:
        common_params["minCitationCount"] = min_citation_count
    if publication_types:
        common_params["publicationTypes"] = publication_types
    if open_access_pdf:
        common_params["openAccessPdf"] = ""

    # The relevance-search endpoint accepts at most 100 results per request.
    # Paginate here so callers can still request a larger aggregate safely.
    collected: list[dict[str, Any]] = []
    current_offset = offset
    total: int | None = None
    while len(collected) < max_results:
        page_limit = min(_SEARCH_PAGE_SIZE, max_results - len(collected))
        params = {**common_params, "limit": page_limit, "offset": current_offset}
        url = f"{_API_BASE}/paper/search?{urllib.parse.urlencode(params)}"
        request_options = {}
        if retries is not None:
            request_options["retries"] = retries
        if retry_delay is not None:
            request_options["retry_delay"] = retry_delay
        if timeout is not None:
            request_options["timeout"] = timeout
        payload = _request_json(url, **request_options)

        page = payload.get("data") or []
        parsed_page = [_parse_paper(item) for item in page]
        collected.extend(parsed_page)
        total = payload.get("total")
        current_offset += len(page)
        if not page or len(page) < page_limit or (total is not None and current_offset >= total):
            break

    return {
        "mode": "search",
        "total": total,
        "offset": offset,
        "next_offset": offset + len(collected),
        "data": collected[:max_results],
    }


def search_bulk(
    query: str,
    max_results: int = 100,
    token: str | None = None,
    fields: str = _DEFAULT_BULK_FIELDS,
    sort: str | None = None,
    fields_of_study: str | None = None,
    venue: str | None = None,
    year: str | None = None,
    min_citation_count: int | None = None,
    publication_types: str | None = None,
    open_access_pdf: bool = False,
    retries: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    max_results = min(max_results, _BULK_PAGE_SIZE)
    params: dict[str, Any] = {
        "query": query,
        "limit": max_results,
        "fields": fields,
    }
    if token:
        params["token"] = token
    if sort:
        params["sort"] = sort
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study
    if venue:
        params["venue"] = venue
    if year:
        params["year"] = year
    if min_citation_count is not None:
        params["minCitationCount"] = min_citation_count
    if publication_types:
        params["publicationTypes"] = publication_types
    if open_access_pdf:
        params["openAccessPdf"] = ""

    url = f"{_API_BASE}/paper/search/bulk?{urllib.parse.urlencode(params)}"
    request_options = {}
    if retries is not None:
        request_options["retries"] = retries
    if timeout is not None:
        request_options["timeout"] = timeout
    payload = _request_json(url, **request_options)

    raw_data = payload.get("data") or []
    data = raw_data[:max_results]
    return {
        "mode": "search-bulk",
        "token": payload.get("token"),
        "returned": len(data),
        "api_returned": len(raw_data),
        "sort": sort,
        "data": [_parse_paper(item) for item in data],
    }


def get_paper(
    paper_id: str, fields: str = _DEFAULT_FIELDS, *, retries: int | None = None,
    retry_delay: float | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    encoded_id = urllib.parse.quote(paper_id, safe="")
    params = {"fields": fields}
    url = f"{_API_BASE}/paper/{encoded_id}?{urllib.parse.urlencode(params)}"
    request_options = {}
    if retries is not None:
        request_options["retries"] = retries
    if retry_delay is not None:
        request_options["retry_delay"] = retry_delay
    if timeout is not None:
        request_options["timeout"] = timeout
    payload = _request_json(url, **request_options)
    return _parse_paper(payload)


_REFERENCES_DEFAULT_FIELDS = "paperId,externalIds,title"


def get_references(
    paper_id: str, fields: str = _REFERENCES_DEFAULT_FIELDS, limit: int = 500,
    *, retries: int | None = None, timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Return the papers `paper_id` itself cites (its own forward reference
    list), via S2's dedicated /paper/{id}/references endpoint (up to the
    API's max of 1000). Each entry is parsed the same shape as get_paper's
    result. Distinct from citationCount/referenceCount (bare numbers already
    available on get_paper/search results) — this is the actual paper list,
    which nothing else in this file previously fetched.
    """
    encoded_id = urllib.parse.quote(paper_id, safe="")
    params = {"fields": fields, "limit": limit}
    url = f"{_API_BASE}/paper/{encoded_id}/references?{urllib.parse.urlencode(params)}"
    request_options = {}
    if retries is not None:
        request_options["retries"] = retries
    if timeout is not None:
        request_options["timeout"] = timeout
    payload = _request_json(url, **request_options)
    data = payload.get("data") or []
    return [_parse_paper(item.get("citedPaper") or {}) for item in data]


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    """Add shared filtering arguments to a search sub-parser."""
    parser.add_argument(
        "--fields-of-study",
        default=None,
        help="Comma-separated fields of study filter, e.g. 'Computer Science,Engineering'.",
    )
    parser.add_argument(
        "--venue",
        default=None,
        help="Comma-separated venue filter, e.g. 'IEEE,ACM' or 'Nature'.",
    )
    parser.add_argument(
        "--year",
        default=None,
        help="Year or range, e.g. '2023', '2020-2024', '2020-', '-2023'.",
    )
    parser.add_argument(
        "--min-citations",
        type=int,
        default=None,
        metavar="N",
        help="Minimum citation count filter.",
    )
    parser.add_argument(
        "--publication-types",
        default=None,
        help="Comma-separated types: JournalArticle,Conference,Review,etc.",
    )
    parser.add_argument(
        "--open-access",
        action="store_true",
        default=False,
        help="Only return papers with a public PDF.",
    )


def _add_request_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--retries", type=int, default=_DEFAULT_RETRIES,
        help=f"Retries after the first request (default: {_DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "--timeout", type=int, default=_DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {_DEFAULT_TIMEOUT}).",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search and fetch papers from Semantic Scholar.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Relevance search for papers")
    search_parser.add_argument("query", help="Keyword query")
    search_parser.add_argument(
        "--max",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of results to return (default: 10).",
    )
    search_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset for pagination (default: 0).",
    )
    search_parser.add_argument(
        "--fields",
        default=_DEFAULT_FIELDS,
        help="Comma-separated response fields to request.",
    )
    _add_filter_args(search_parser)
    _add_request_args(search_parser)

    bulk_parser = subparsers.add_parser(
        "search-bulk",
        help="Bulk search for papers with token-based pagination",
    )
    bulk_parser.add_argument("query", help="Keyword query")
    bulk_parser.add_argument(
        "--max",
        type=int,
        default=100,
        metavar="N",
        help="Maximum number of results to return in this page (default: 100).",
    )
    bulk_parser.add_argument(
        "--token",
        default=None,
        help="Continuation token returned by a previous bulk search page.",
    )
    bulk_parser.add_argument(
        "--sort",
        default=None,
        help="Optional sort for bulk search, e.g. publicationDate:desc or citationCount:desc",
    )
    bulk_parser.add_argument(
        "--fields",
        default=_DEFAULT_BULK_FIELDS,
        help="Comma-separated response fields to request.",
    )
    _add_filter_args(bulk_parser)
    _add_request_args(bulk_parser)

    paper_parser = subparsers.add_parser("paper", help="Fetch one paper by ID")
    paper_parser.add_argument(
        "id",
        help=(
            "Semantic Scholar paper ID, DOI, CorpusId:..., ARXIV:..., PMID:..., MAG:..., ACL:..., etc."
        ),
    )
    _add_request_args(paper_parser)
    paper_parser.add_argument(
        "--fields",
        default=_DEFAULT_FIELDS,
        help="Comma-separated response fields to request.",
    )

    references_parser = subparsers.add_parser(
        "references", help="List the papers a given paper cites (its own forward reference list)"
    )
    references_parser.add_argument(
        "id",
        help=(
            "Semantic Scholar paper ID, DOI, CorpusId:..., ARXIV:..., PMID:..., MAG:..., ACL:..., etc."
        ),
    )
    _add_request_args(references_parser)
    references_parser.add_argument(
        "--fields",
        default=_REFERENCES_DEFAULT_FIELDS,
        help="Comma-separated response fields per referenced paper (default: paperId,externalIds,title).",
    )
    references_parser.add_argument(
        "--limit",
        type=int,
        default=500,
        metavar="N",
        help="Max references to return (default: 500, API max: 1000).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        if args.command == "search":
            result = search(
                query=args.query,
                max_results=args.max,
                offset=args.offset,
                fields=args.fields,
                fields_of_study=args.fields_of_study,
                venue=args.venue,
                year=args.year,
                min_citation_count=args.min_citations,
                publication_types=args.publication_types,
                open_access_pdf=args.open_access,
                retries=args.retries,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "search-bulk":
            result = search_bulk(
                query=args.query,
                max_results=args.max,
                token=args.token,
                fields=args.fields,
                sort=args.sort,
                fields_of_study=args.fields_of_study,
                venue=args.venue,
                year=args.year,
                min_citation_count=args.min_citations,
                publication_types=args.publication_types,
                open_access_pdf=args.open_access,
                retries=args.retries,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "paper":
            result = get_paper(
                paper_id=args.id,
                fields=args.fields,
                retries=args.retries,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "references":
            result = get_references(
                paper_id=args.id,
                fields=args.fields,
                limit=args.limit,
                retries=args.retries,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        raise ValueError(f"Unsupported command: {args.command}")

    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
