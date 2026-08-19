#!/usr/bin/env python3
"""Fetch and search Hugging Face Daily Papers with a local date cache.

Examples
--------
python3 tools/huggingface_papers_fetch.py search --days 7 --min-upvotes 50
python3 tools/huggingface_papers_fetch.py search --days 14 --query "Agent Memory"
python3 tools/huggingface_papers_fetch.py fetch --days 3 --refresh
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_query_match as query_match  # noqa: E402

_API_BASE = "https://huggingface.co/api/daily_papers"
_DEFAULT_CACHE_DIR = Path("output/huggingface_papers")
_FULL_CACHE_SUBDIR = "daily_full"
_DEFAULT_MIN_UPVOTES = 50
_USER_AGENT = "research-agent-huggingface-papers/1.0"
_MAX_PAGES_PER_DAY = 20
_REFETCH_WINDOW_DAYS = 30
_REQUEST_HOOK: Callable[[], None] | None = None


def set_request_hook(hook: Callable[[], None] | None) -> None:
    """Set an optional caller-owned hook that runs before every HTTP attempt."""
    global _REQUEST_HOOK
    _REQUEST_HOOK = hook


def _run_request_hook() -> None:
    if _REQUEST_HOOK is not None:
        _REQUEST_HOOK()


@dataclass(frozen=True)
class SearchResult:
    paper: dict[str, Any]
    match_score: int
    matched_queries: list[str]


def parse_iso_date(value: str) -> date:
    """Parse YYYY-MM-DD into a date."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from exc


def date_range(days: int, until: date | None = None) -> list[date]:
    """Return an inclusive recent date window ending at ``until``."""
    if days < 1:
        raise ValueError("--days must be >= 1")
    end = until or date.today()
    start = end - timedelta(days=days - 1)
    return [start + timedelta(days=offset) for offset in range(days)]


def _validate_min_upvotes(min_upvotes: int) -> int:
    if min_upvotes < 0:
        raise ValueError("--min-upvotes must be >= 0")
    return min_upvotes


def _cache_path_for_fetch_day(cache_dir: Path, day: date, fetch_day: date) -> Path:
    return cache_dir / _FULL_CACHE_SUBDIR / fetch_day.isoformat() / f"{day.isoformat()}.json"


def _parse_cached_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_cache_file(cache_dir: Path, day: date) -> tuple[date, Path] | None:
    root = cache_dir / _FULL_CACHE_SUBDIR
    if not root.exists():
        return None
    matches = []
    for child in root.iterdir():
        fetch_day = _parse_cached_date(child.name)
        path = child / f"{day.isoformat()}.json"
        if fetch_day and path.is_file():
            matches.append((fetch_day, path))
    return max(matches, default=None, key=lambda item: item[0])


def _should_refetch_cache(fetch_day: date, day: date) -> bool:
    return (fetch_day - day).days <= _REFETCH_WINDOW_DAYS


def _request_json(url: str, *, retries: int = 2, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _run_request_hook()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                last_error = exc
                continue
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            message = f"HTTP {exc.code}"
            if body:
                message += f": {body}"
            raise RuntimeError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                last_error = exc
                continue
            raise RuntimeError(f"Network error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse JSON response from Hugging Face") from exc
    raise RuntimeError(f"Request failed after retries: {last_error}")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    return text or None


def _author_name(author: dict[str, Any]) -> str | None:
    return _clean_text(author.get("name") or (author.get("user") or {}).get("fullname"))


def normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a daily-paper wrapper or paper object into stable fields."""
    paper = item.get("paper") if isinstance(item.get("paper"), dict) else item
    paper_id = _clean_text(paper.get("id"))
    authors = [
        name
        for name in (_author_name(author) for author in paper.get("authors") or [])
        if name
    ]
    return {
        "id": paper_id,
        "title": _clean_text(paper.get("title")),
        "authors": authors,
        "summary": _clean_text(paper.get("summary")),
        "ai_summary": _clean_text(paper.get("ai_summary")),
        "ai_keywords": [
            _clean_text(keyword)
            for keyword in paper.get("ai_keywords") or []
            if _clean_text(keyword)
        ],
        "upvotes": int(paper.get("upvotes") or 0),
        "publishedAt": _clean_text(paper.get("publishedAt")),
        "submittedOnDailyAt": _clean_text(paper.get("submittedOnDailyAt")),
        "url": f"https://huggingface.co/papers/{paper_id}" if paper_id else None,
        "abs_url": f"https://arxiv.org/abs/{paper_id}" if paper_id else None,
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}.pdf" if paper_id else None,
        "githubRepo": _clean_text(paper.get("githubRepo")),
        "projectPage": _clean_text(paper.get("projectPage")),
    }


def _dedupe_papers(papers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for paper in papers:
        paper_id = paper.get("id")
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        result.append(paper)
    return result


def fetch_daily(day: date, *, max_pages: int = _MAX_PAGES_PER_DAY) -> list[dict[str, Any]]:
    """Fetch one Hugging Face Daily Papers date from the public API."""
    papers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    date_param = day.isoformat()

    for page in range(max_pages):
        params: dict[str, str | int] = {"date": date_param}
        if page > 0:
            params["p"] = page
        url = f"{_API_BASE}?{urllib.parse.urlencode(params)}"
        payload = _request_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected Hugging Face response for {date_param}: {type(payload)}")
        if not payload:
            break

        new_count = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            normalized = normalize_paper(item)
            paper_id = normalized.get("id")
            if not paper_id or paper_id in seen_ids:
                continue
            seen_ids.add(paper_id)
            papers.append(normalized)
            new_count += 1
        if new_count == 0:
            break

    return papers


def load_cached_day(cache_dir: Path, day: date) -> list[dict[str, Any]] | None:
    """Return cached papers, or None when the cache is absent/invalid."""
    hit = _latest_cache_file(cache_dir, day)
    if hit is None:
        return None
    fetch_day, path = hit
    if _should_refetch_cache(fetch_day, day):
        return None
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    papers = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(papers, list):
        return None
    normalized = [normalize_paper(item) if "paper" in item else item for item in papers]
    return _dedupe_papers(p for p in normalized if isinstance(p, dict))


def save_cached_day(
    cache_dir: Path,
    day: date,
    papers: list[dict[str, Any]],
    fetch_day: date | None = None,
) -> Path:
    """Write one full daily cache file."""
    path = _cache_path_for_fetch_day(cache_dir, day, fetch_day or date.today())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day.isoformat(),
        "source": f"{_API_BASE}?{urllib.parse.urlencode({'date': day.isoformat()})}",
        "papers": papers,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def get_daily(cache_dir: Path, day: date, *, refresh: bool = False) -> tuple[list[dict[str, Any]], bool]:
    """Load one day from cache, fetching and saving when needed.

    Returns ``(papers, fetched)`` where fetched is True only for network refreshes.
    """
    if not refresh:
        cached = load_cached_day(cache_dir, day)
        if cached is not None:
            return cached, False

    papers = fetch_daily(day)
    save_cached_day(cache_dir, day, papers)
    return papers, True


def get_window(
    days: int,
    *,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
    refresh: bool = False,
    until: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch/load a recent full-cache window and return deduplicated papers plus stats."""
    all_papers: list[dict[str, Any]] = []
    fetched_dates: list[str] = []
    cached_dates: list[str] = []

    for day in date_range(days, until=until):
        papers, fetched = get_daily(
            cache_dir,
            day,
            refresh=refresh,
        )
        all_papers.extend(papers)
        (fetched_dates if fetched else cached_dates).append(day.isoformat())

    papers = _dedupe_papers(all_papers)
    return papers, {
        "cache_dir": str(cache_dir),
        "full_cache_dir": str(cache_dir / _FULL_CACHE_SUBDIR),
        "days": days,
        "fetched_dates": fetched_dates,
        "cached_dates": cached_dates,
        "total_unique_full": len(papers),
    }


def _paper_date(paper: dict[str, Any]) -> str:
    return str(paper.get("submittedOnDailyAt") or paper.get("publishedAt") or "")


def _match_text_parts(paper: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in ("title", "summary", "ai_summary"):
        value = paper.get(key)
        if value:
            parts.append(str(value))
    parts.extend(str(keyword) for keyword in paper.get("ai_keywords") or [])
    return parts


def _search_blob(paper: dict[str, Any]) -> str:
    return "\n".join(_match_text_parts(paper))


def search_papers(
    papers: Iterable[dict[str, Any]],
    *,
    min_upvotes: int = 0,
    query: str | None = None,
) -> list[SearchResult]:
    """Filter and rank papers by upvotes and optional shared research query matcher."""
    query_strings = [query] if query else []
    results: list[SearchResult] = []

    for paper in papers:
        if int(paper.get("upvotes") or 0) < min_upvotes:
            continue
        searchable_text = _search_blob(paper)
        matched_queries = query_match.matched_queries(searchable_text, query_strings)
        if query_strings and not matched_queries:
            continue
        results.append(
            SearchResult(
                paper=paper,
                match_score=len(matched_queries),
                matched_queries=matched_queries,
            )
        )

    if query_strings:
        return sorted(
            results,
            key=lambda item: (
                item.match_score,
                int(item.paper.get("upvotes") or 0),
                _paper_date(item.paper),
            ),
            reverse=True,
        )
    return sorted(
        results,
        key=lambda item: (int(item.paper.get("upvotes") or 0), _paper_date(item.paper)),
        reverse=True,
    )


def _result_to_dict(result: SearchResult) -> dict[str, Any]:
    return {
        **result.paper,
        "match_score": result.match_score,
        "matched_queries": result.matched_queries,
    }


def render_markdown(results: list[SearchResult], stats: dict[str, Any]) -> str:
    lines = [
        f"# Hugging Face Papers ({len(results)} results)",
        "",
        f"- cache_dir: `{stats['cache_dir']}`",
        f"- fetched_dates: {', '.join(stats['fetched_dates']) or 'none'}",
        f"- cached_dates: {', '.join(stats['cached_dates']) or 'none'}",
        "",
    ]
    for idx, result in enumerate(results, start=1):
        paper = result.paper
        title = paper.get("title") or paper.get("id") or "Untitled"
        lines.extend(
            [
                f"## {idx}. {title}",
                "",
                f"- id: `{paper.get('id')}`",
                f"- upvotes: {paper.get('upvotes', 0)}",
                f"- date: {_paper_date(paper)[:10] or 'unknown'}",
                f"- url: {paper.get('url')}",
                f"- match_score: {result.match_score}",
                f"- matched_queries: {', '.join(result.matched_queries) or 'none'}",
                "",
            ]
        )
        summary = paper.get("ai_summary") or paper.get("summary")
        if summary:
            lines.extend([str(summary), ""])
    return "\n".join(lines).rstrip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and search Hugging Face Daily Papers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--days", type=int, default=7, help="Recent days to inspect (default: 7).")
        p.add_argument(
            "--until",
            type=parse_iso_date,
            help="End date for the window, YYYY-MM-DD (default: local today).",
        )
        p.add_argument(
            "--cache-dir",
            type=Path,
            default=_DEFAULT_CACHE_DIR,
            help="Cache directory (default: output/huggingface_papers).",
        )
        p.add_argument("--refresh", action="store_true", help="Refetch dates even when cached.")

    search_parser = subparsers.add_parser("search", help="Search cached/fetched daily papers.")
    add_common_args(search_parser)
    search_parser.add_argument(
        "--min-upvotes",
        type=int,
        default=_DEFAULT_MIN_UPVOTES,
        help=f"Minimum upvotes for local search filtering (default: {_DEFAULT_MIN_UPVOTES}).",
    )
    search_parser.add_argument(
        "--query",
        help="Local query over title/summary/keywords; spaces mean AND, OR is supported, double quotes mean phrase.",
    )
    search_parser.add_argument(
        "--format",
        choices=("json", "md"),
        default="json",
        help="Output format (default: json).",
    )

    fetch_parser = subparsers.add_parser("fetch", help="Populate the daily cache only.")
    add_common_args(fetch_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command_names = {"search", "fetch"}
    if raw_argv and raw_argv[0] not in command_names and raw_argv[0] not in ("-h", "--help"):
        raw_argv.insert(0, "search")
    args = _build_parser().parse_args(raw_argv)
    if args.command is None:
        args = _build_parser().parse_args(["search"])

    papers, stats = get_window(
        args.days,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
        until=args.until,
    )

    if args.command == "fetch":
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "search":
        results = search_papers(papers, min_upvotes=args.min_upvotes, query=args.query)
        if args.format == "md":
            print(render_markdown(results, stats), end="")
        else:
            print(
                json.dumps(
                    {
                        "stats": {
                            **stats,
                            "result_count": len(results),
                            "min_upvotes": args.min_upvotes,
                            "query": args.query,
                        },
                        "papers": [_result_to_dict(result) for result in results],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
