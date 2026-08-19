#!/usr/bin/env python3
"""CLI helper for searching and downloading arXiv papers.

Used by the ``research-lit`` skill (skills/research-lit/SKILL.md).

Commands
--------
search    Search arXiv and print results as JSON.
download  Download a paper PDF by arXiv ID.

Examples
--------
python3 tools/arxiv_fetch.py search "attention mechanism" --max 10
python3 tools/arxiv_fetch.py search "id:2301.07041" --max 1
python3 tools/arxiv_fetch.py search "attention mechanism" --count-only
python3 tools/arxiv_fetch.py download 2301.07041 --dir papers
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

_ATOM_NS = "http://www.w3.org/2005/Atom"
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
# https, not http: arXiv now permanently redirects plain HTTP requests to
# HTTPS (confirmed via a live 301 with a Strict-Transport-Security header).
# Under some proxy configurations that redirect doesn't resolve cleanly and
# urllib gives up entirely ("redirect error that would lead to an infinite
# loop") instead of just following it once — using https:// from the start
# sidesteps the redirect (and that failure mode) altogether.
_API_BASE = "https://export.arxiv.org/api/query"
_MIN_PDF_BYTES = 10_240
_REQUEST_HOOK: Callable[[], None] | None = None


def set_request_hook(hook: Callable[[], None] | None) -> None:
    """Set an optional caller-owned hook that runs before every HTTP attempt."""
    global _REQUEST_HOOK
    _REQUEST_HOOK = hook


def _run_request_hook() -> None:
    if _REQUEST_HOOK is not None:
        _REQUEST_HOOK()


def _ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context backed by certifi's CA bundle when available.

    Common on a fresh python.org macOS install: urllib's default context has
    no CA bundle configured (the installer's "Install Certificates.command"
    step is easy to skip), so every HTTPS request fails with
    CERTIFICATE_VERIFY_FAILED even though curl/browsers work fine (they use
    the system keychain instead). certifi ships its own trusted CA bundle;
    when installed, use it explicitly instead of relying on the interpreter's
    unconfigured default. Returns None (caller uses urllib's own default) if
    certifi isn't installed — this is a resilience improvement, not a new
    hard dependency.
    """
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


_RATE_LIMIT_STATE_FILE = Path(tempfile.gettempdir()) / "aris_arxiv_last_request"
_MIN_REQUEST_INTERVAL = float(os.environ.get("ARXIV_MIN_REQUEST_INTERVAL", "3.0"))


def _throttle() -> None:
    """Enforce a minimum spacing between arXiv API requests.

    arXiv's API terms ask for at most one request per 3 seconds from a single
    source. A single process's own retry/backoff (below) can only pace *its
    own* retries — it has no way to know that some other, separate
    invocation of this script fired a request two seconds ago. In practice
    this script gets invoked repeatedly and independently (a search, then a
    count-only probe, then a handful of downloads, each its own `python3
    arxiv_fetch.py ...` process), so the pacing has to survive across
    process boundaries. A small timestamp file in the system temp dir does
    that cheaply: every invocation checks how long it's been since the last
    recorded request (from *any* invocation) and sleeps out the remainder of
    `_MIN_REQUEST_INTERVAL` before proceeding, then updates the timestamp.
    Override the interval via `ARXIV_MIN_REQUEST_INTERVAL` if 3.0s proves
    too conservative or not conservative enough.
    """
    now = time.time()
    try:
        last = float(_RATE_LIMIT_STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        last = 0.0
    wait = _MIN_REQUEST_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    try:
        _RATE_LIMIT_STATE_FILE.write_text(str(time.time()))
    except OSError:
        pass


def _arxiv_user_agent() -> str:
    """Descriptive User-Agent for arXiv API calls.

    arXiv rate-limits the default ``Python-urllib/x.y`` agent far more
    aggressively than a named client; sending a descriptive UA (with an
    optional contact address) lands requests in arXiv's more lenient pool.
    The contact is read from ``ARIS_VERIFY_EMAIL`` — the same env var
    ``tools/research_wiki.py`` and ``tools/verify_papers.py`` already use —
    so no address is hard-coded. Falls back to a contactless UA when unset.
    """
    contact = os.environ.get("ARIS_VERIFY_EMAIL", "").strip()
    base = ("arxiv-skill/1.0 "
            "(+https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep)")
    return f"{base} (mailto:{contact})" if contact else base


_NEW_STYLE_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_OLD_STYLE_ID_RE = re.compile(r"^[A-Za-z.-]+/\d{7}(v\d+)?$")


def _normalize_id(arxiv_id: str) -> str:
    """Strip URL/version noise and return a clean arXiv ID."""
    value = arxiv_id.strip()
    if "/abs/" in value:
        value = value.split("/abs/", 1)[1]
    if value.startswith("id:"):
        value = value[3:]
    if "v" in value.split(".")[-1]:
        value = value.rsplit("v", 1)[0]
    return value


def _looks_like_arxiv_id(value: str) -> bool:
    """Return True when the input resembles a modern or legacy arXiv ID."""
    value = value.strip()
    return bool(_NEW_STYLE_ID_RE.match(value) or _OLD_STYLE_ID_RE.match(value))


_ARXIV_FIELD_PREFIX_RE = re.compile(r"^[A-Za-z_]+:")
_BOOLEAN_SPLIT_RE = re.compile(r"\s+(ANDNOT|AND|OR)\s+")


def _scope_clause(clause: str) -> str:
    """Wrap a bare keyword clause in per-token ``all:`` field scoping.

    A bare clause like ``test-time scaling verifier`` carries no arXiv field
    prefix (``all:``/``ti:``/``abs:``/``cat:``/``submittedDate:`` etc.), so
    passing it to ``search_query`` verbatim leaves it essentially unscoped —
    the arXiv API's ``--count-only`` total stays around 600K regardless of
    the actual query text, and the ``--max``-capped results are just the
    top-N by relevance over that huge, loosely-matched pool rather than
    papers that genuinely contain every query term. Wrapping each token in
    ``all:`` and AND-joining them makes this a real "must contain all these
    terms" search, matching Semantic Scholar's equivalent behavior.
    """
    clause = clause.strip()
    if not clause:
        return clause
    if _ARXIV_FIELD_PREFIX_RE.match(clause):
        # Already field-scoped (e.g. "submittedDate:[...]", "cat:cs.CL",
        # "ti:diffusion") — the caller wrote real arXiv query syntax here;
        # leave it untouched.
        return clause
    return " AND ".join(f"all:{tok}" for tok in clause.split())


def _build_search_query(query: str) -> str:
    """Scope a free-text query into real arXiv ``search_query`` syntax.

    Splits on the boolean connectors arXiv's API understands (``AND``/
    ``OR``/``ANDNOT``), scopes each bare clause via `_scope_clause`, and
    passes through any clause the caller already wrote in explicit field
    syntax unchanged — so a query like ``"test-time scaling verifier AND
    submittedDate:[20240101 TO 20261231]"`` only scopes the bare keyword
    half, leaving the date-range clause exactly as written.
    """
    parts = _BOOLEAN_SPLIT_RE.split(query)
    # re.split with a capturing group alternates [clause, connector, clause, ...].
    return " ".join(
        part if i % 2 == 1 else _scope_clause(part) for i, part in enumerate(parts)
    )


def _api_url(query: str, max_results: int, start: int) -> str:
    """Build the arXiv API URL for a search query or specific ID lookup."""
    query = query.strip()
    if query.startswith("id:"):
        params = {"id_list": _normalize_id(query)}
    elif _looks_like_arxiv_id(query):
        params = {"id_list": _normalize_id(query)}
    else:
        params = {
            "search_query": _build_search_query(query),
            "start": start,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    return f"{_API_BASE}?{urllib.parse.urlencode(params)}"


_MAX_ATTEMPTS = 5


def _fetch_atom(url: str) -> ET.Element:
    """Fetch an arXiv Atom feed and return the parsed XML root.

    Sends a descriptive User-Agent (landing requests in arXiv's lenient pool),
    calls `_throttle()` before every attempt (including retries — a retry is
    still a request, and still subject to the same minimum spacing), and
    retries up to `_MAX_ATTEMPTS` times on HTTP 429, transient network
    errors, and the plain-text ``Rate exceeded.`` body the API sometimes
    returns with 200 OK. 429/rate-exceeded responses back off much longer
    than a generic transient error (20s, 40s, 60s, 80s...) because they mean
    we're already past arXiv's rate limit, not just hitting an ordinary
    network blip — a cooldown measured in tens of seconds, confirmed in
    practice, not a few seconds. Slower and reliable beats fast and 429'd.
    Raises RuntimeError when all retries are exhausted.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _arxiv_user_agent()})
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        _throttle()
        _run_request_hook()
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _MAX_ATTEMPTS:
                time.sleep(20 * attempt)
                continue
            raise RuntimeError(f"arXiv API fetch failed: {e}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"arXiv API fetch failed: {e}")
        if body.strip() == b"Rate exceeded.":
            if attempt < _MAX_ATTEMPTS:
                time.sleep(20 * attempt)
                continue
            raise RuntimeError(f"arXiv API rate-limited after {_MAX_ATTEMPTS} attempts")
        return ET.fromstring(body)
    # unreachable; loop either returns or raises
    raise RuntimeError("arXiv API fetch failed: exhausted retries")


_ACCEPTED_VENUE_RE = re.compile(
    r"\b(?:accepted\s+(?:at|to)|accepted\s+for\s+publication\s+(?:at|in)|to\s+appear\s+(?:at|in))\s+([^.;\n]+)",
    re.IGNORECASE,
)


def _venue_from_comment(comment: str | None) -> str:
    """Return an explicit accepted venue, otherwise the arXiv-only default."""
    if not comment:
        return "arXiv preprint"
    match = _ACCEPTED_VENUE_RE.search(comment)
    if not match:
        return "arXiv preprint"
    venue = match.group(1).strip()
    venue = re.sub(
        r",\s*(?:\d+\s+pages?.*|\d+\s+figures?.*|code\s+(?:is\s+)?available.*)$",
        "",
        venue,
        flags=re.IGNORECASE,
    ).strip()
    return venue[:160] if venue else "arXiv preprint"


def _parse_entry(entry: ET.Element) -> dict:
    """Extract structured fields from a single Atom <entry> element."""
    raw_id = entry.findtext(f"{{{_ATOM_NS}}}id", "")
    arxiv_id = _normalize_id(raw_id)
    title = (entry.findtext(f"{{{_ATOM_NS}}}title", "") or "").strip().replace("\n", " ")
    abstract = (entry.findtext(f"{{{_ATOM_NS}}}summary", "") or "").strip().replace("\n", " ")
    published = (entry.findtext(f"{{{_ATOM_NS}}}published", "") or "")[:10]
    updated = (entry.findtext(f"{{{_ATOM_NS}}}updated", "") or "")[:10]
    authors = [
        author.findtext(f"{{{_ATOM_NS}}}name", "")
        for author in entry.findall(f"{{{_ATOM_NS}}}author")
    ]
    categories = [
        category.get("term", "")
        for category in entry.findall(f"{{{_ATOM_NS}}}category")
        if category.get("term")
    ]
    # Author-submitted free text, often "Accepted at NeurIPS 2026" / "To appear
    # in ACL 2026" for preprints pending or already accepted at a venue — a
    # free, no-extra-fetch venue signal for papers not yet indexed by S2.
    comment = (entry.findtext(f"{{{_ARXIV_NS}}}comment", "") or "").strip().replace("\n", " ")
    return {
        "id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "published": published,
        "updated": updated,
        "categories": categories,
        "comment": comment or None,
        "venue": _venue_from_comment(comment),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def search(query: str, max_results: int = 10, start: int = 0) -> list[dict]:
    """Search arXiv and return a list of paper dictionaries."""
    url = _api_url(query, max_results=max_results, start=start)
    root = _fetch_atom(url)
    return [_parse_entry(entry) for entry in root.findall(f"{{{_ATOM_NS}}}entry")]


def count(query: str) -> int:
    """Return arXiv's reported total hit count for a query, without fetching entries.

    Used for scope probing (e.g. research-lit's Stage 2.1) — cheap enough to run
    before committing to a full search. Reads ``opensearch:totalResults``,
    which the Atom feed always includes alongside the entry list but which
    ``search()`` otherwise discards.
    """
    # max_results=0 triggers an HTTP 500 from arXiv's real API (confirmed
    # live, not a mock artifact) — 1 is the minimum value that both works
    # and still keeps the response small; totalResults is present either way.
    url = _api_url(query, max_results=1, start=0)
    root = _fetch_atom(url)
    total_el = root.find(f"{{{_OPENSEARCH_NS}}}totalResults")
    return int(total_el.text) if total_el is not None and total_el.text else 0


def _pdf_content_matches_id(data: bytes, clean_id: str) -> bool | None:
    """Best-effort check that downloaded PDF bytes are actually the requested paper.

    Confirmed in practice, not hypothetical: a download once returned 200 OK,
    passed the size check, and was written to disk under one arXiv ID's
    filename while its actual content was a completely different paper (a
    transient bad response somewhere in the network path — a direct re-fetch
    of the same URL immediately after returned the correct paper, so this
    wasn't a code bug, just an unverified assumption that "200 OK plus a
    plausible size" means "the right content"). arXiv's own LaTeX template
    stamps ``arXiv:<id>`` as a running header/footer on most (not
    all — depends on the submitter's template) generated PDFs, so this is a
    real but imperfect signal: True/False when checked, None when it
    genuinely can't be checked (pdftotext missing, or extraction failed) —
    callers should treat None as "no verdict," not as a failure.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(data)
            tmp.flush()
            result = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "2", tmp.name, "-"],
                capture_output=True, timeout=20, text=True,
            )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Strict digits-only match on the id itself (ignore any version suffix
    # the stamped text may or may not include) — avoid false negatives from
    # "v1" vs "v2" mismatches between what we requested and what's stamped.
    bare_id = clean_id.split("v")[0] if re.search(r"v\d+$", clean_id) else clean_id
    return bare_id in result.stdout


def download(arxiv_id: str, output_dir: str = "papers") -> dict:
    """Download a paper PDF and return metadata about the saved file."""
    clean_id = _normalize_id(arxiv_id)
    safe_id = clean_id.replace("/", "_")

    dest_dir = Path(output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_id}.pdf"

    if dest.exists():
        return {
            "id": clean_id,
            "path": str(dest),
            "size_kb": dest.stat().st_size // 1024,
            "skipped": True,
        }

    pdf_url = f"https://arxiv.org/pdf/{clean_id}.pdf"
    req = urllib.request.Request(pdf_url, headers={"User-Agent": _arxiv_user_agent()})

    content_attempts = 2  # one retry if content verification fails, not just on network errors
    for content_attempt in range(1, content_attempts + 1):
        data = b""
        for attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:
                    data = resp.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise RuntimeError(f"Failed to download {pdf_url}: {exc}")
        else:
            raise RuntimeError(f"Failed to download {pdf_url} after 3 attempts")

        if len(data) < _MIN_PDF_BYTES:
            raise ValueError(
                f"Downloaded file is only {len(data)} bytes - likely an error page, not a PDF"
            )

        verdict = _pdf_content_matches_id(data, clean_id)
        if verdict is not False:
            # True (confirmed match) or None (couldn't check) both proceed —
            # only a confirmed mismatch triggers a retry/failure.
            break
        if content_attempt < content_attempts:
            continue
        raise RuntimeError(
            f"Downloaded PDF for {clean_id} does not appear to contain that arXiv id "
            f"anywhere in its first two pages after {content_attempts} attempts — "
            f"the content doesn't match the requested paper. Not writing it to disk."
        )

    dest.write_bytes(data)
    return {
        "id": clean_id,
        "path": str(dest),
        "size_kb": len(data) // 1024,
        "skipped": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search and download arXiv papers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_search_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "query",
            help="Search query or arXiv ID (bare ID or id:ARXIV_ID).",
        )
        p.add_argument(
            "--max",
            type=int,
            default=10,
            metavar="N",
            help="Maximum number of results (default: 10).",
        )
        p.add_argument(
            "--start",
            type=int,
            default=0,
            help="Start offset for pagination (default: 0).",
        )
        p.add_argument(
            "--count-only",
            action="store_true",
            help=(
                'Print only {"total_results": N} via opensearch:totalResults, '
                "without fetching any entries. Ignores --max/--start."
            ),
        )

    search_parser = subparsers.add_parser("search", help="Search arXiv papers")
    _add_search_args(search_parser)

    # Defensive aliases — models frequently hallucinate `get` / `fetch`
    # instead of `search`.  Accept them silently so the invocation succeeds
    # regardless of model quality.
    for alias in ("get", "fetch"):
        _add_search_args(subparsers.add_parser(alias, help="Alias for search"))

    download_parser = subparsers.add_parser("download", help="Download a paper PDF by arXiv ID")
    download_parser.add_argument(
        "id",
        help="arXiv paper ID, e.g. 2301.07041 or cs/0601001",
    )
    download_parser.add_argument(
        "--dir",
        default="papers",
        metavar="DIR",
        help="Output directory (default: papers).",
    )
    download_parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to sleep after download (default: 2.0).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command in ("search", "get", "fetch"):
            if args.count_only:
                print(json.dumps({"total_results": count(args.query)}, ensure_ascii=False))
                return 0
            results = search(args.query, max_results=args.max, start=args.start)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0

        if args.command == "download":
            result = download(args.id, output_dir=args.dir)
            if result.get("skipped"):
                print(json.dumps({**result, "message": "already exists, skipped"}, ensure_ascii=False))
            else:
                time.sleep(args.delay)
                print(json.dumps(result, ensure_ascii=False))
            return 0

        raise ValueError(f"Unsupported command: {args.command}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
