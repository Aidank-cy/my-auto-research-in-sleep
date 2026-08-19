#!/usr/bin/env python3
"""CLI helper for looking up a paper's Hugging Face community signal.

This is a standalone compatibility helper for the former score-based workflow;
the current `research-lit` workflow obtains Hugging Face signals through
`research_candidate_search.py` and `huggingface_papers_fetch.py`. Citation volume here
comes entirely from the internal reference graph within a run's own
candidate set (see tools/relevance_score.py), which says nothing about a
paper the rest of the set doesn't happen to cite. This tool supplies the
other half — "is the community paying attention to this one right now?" —
via how recently it was featured on HF Daily Papers.

Every arXiv paper gets an auto-created Hugging Face page (upvotes default to
0) the moment it's referenced from any model/dataset/Space README, so a 404
just means zero community engagement so far, not an error.

Returns `submitted_on_daily_at` — the date the paper was last featured on HF
Daily Papers (null if never featured) — which compatibility scoring can use as
a freshness signal. A paper never
featured at all, or featured outside the freshness window, is "no current
signal" — tools/relevance_score.py drops the hf_freshness term entirely for
such a paper and renormalizes the other two weights, rather than treating
the null as a low/neutral value to score against.

Commands
--------
paper   Look up one or more papers' HF pages by arXiv ID.

Examples
--------
python3 tools/huggingface_fetch.py paper 2401.12345
python3 tools/huggingface_fetch.py paper arxiv.org/abs/2401.12345v2
python3 tools/huggingface_fetch.py paper "2401.12345 2402.23456"

Called directly at its fixed repo path (`tools/huggingface_fetch.py`) — this
project has one canonical tools/ location, so there is no dynamic resolution
chain to walk.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://huggingface.co/api/papers"
_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def _ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context backed by certifi's CA bundle when available.

    See arxiv_fetch.py's identical helper for why this exists (a fresh
    python.org macOS install often has no CA bundle wired into urllib's
    default context). Returns None (urllib's own default) if certifi isn't
    installed.
    """
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _normalize_id(arxiv_id: str) -> str:
    """Strip URL/version noise and return a clean arXiv ID."""
    value = arxiv_id.strip()
    if "/abs/" in value:
        value = value.split("/abs/", 1)[1]
    if value.lower().startswith("arxiv:"):
        value = value[len("arxiv:"):]
    if "v" in value.split(".")[-1]:
        value = value.rsplit("v", 1)[0]
    return value


def _split_ids(values: list[str]) -> list[str]:
    """Accept normal argv tokens or a mistakenly quoted whitespace list."""
    ids: list[str] = []
    for value in values:
        ids.extend(part for part in re.split(r"[\s,]+", value.strip()) if part)
    return ids


def _not_found(clean_id: str) -> dict:
    return {
        "id": clean_id,
        "on_huggingface": False,
        "submitted_on_daily_at": None,
        "upvotes": 0,
        "title": None,
        "url": f"https://huggingface.co/papers/{clean_id}",
    }


def paper(arxiv_id: str, *, retries: int = 3, delay: float = 2.0) -> dict:
    """Look up one paper's Hugging Face page by arXiv ID.

    Returns `{"id", "on_huggingface", "submitted_on_daily_at", "upvotes",
    "title", "url"}`. `submitted_on_daily_at` is the ISO date the paper was
    last featured on HF Daily Papers, or `null` if it never was — the caller
    (tools/relevance_score.py) treats that `null`, and any date outside its
    freshness window, as "no current signal" and excludes the hf_freshness
    term for that paper rather than scoring it as low/neutral. A
    404 (paper has no HF page at all yet) is likewise a normal outcome, not
    an error — it comes back with every field at its lowest/absent value,
    never raises.

    Retries on 429/5xx and transient network errors, with linear backoff
    (`delay`, `2*delay`, ...), matching the resilience of this project's other
    fetch tools (arxiv_fetch.py, semantic_scholar_fetch.py) rather than
    surfacing a single transient blip as a hard failure.
    """
    clean_id = _normalize_id(arxiv_id)
    if not _ID_RE.match(clean_id):
        raise ValueError(f"Invalid arXiv ID for Hugging Face lookup: {arxiv_id!r}")
    if retries < 0:
        raise ValueError("retries must be >= 0")

    encoded_id = urllib.parse.quote(clean_id, safe="")
    url = f"{_API_BASE}/{encoded_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "research-lit-skill/1.0",
            "Accept": "application/json",
            "Connection": "close",
        },
    )
    data = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return _not_found(clean_id)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    sleep_for = float(retry_after) if retry_after else delay * (attempt + 1)
                except ValueError:
                    sleep_for = delay * (attempt + 1)
                time.sleep(sleep_for)
                continue
            raise RuntimeError(f"Hugging Face API fetch failed: {e}")
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            if attempt < retries:
                time.sleep(delay * (attempt + 1))
                continue
            raise RuntimeError(f"Hugging Face API fetch failed: {e}")
        except json.JSONDecodeError as e:
            raise RuntimeError("Failed to parse JSON response from Hugging Face API") from e

    if not isinstance(data, dict):
        return _not_found(clean_id)
    submitted_on_daily_at = data.get("submittedOnDailyAt")
    return {
        "id": data.get("id", clean_id),
        "on_huggingface": True,
        "submitted_on_daily_at": submitted_on_daily_at[:10] if submitted_on_daily_at else None,
        "upvotes": data.get("upvotes", 0),
        "title": data.get("title"),
        "url": f"https://huggingface.co/papers/{clean_id}",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    paper_parser = subparsers.add_parser("paper", help="Look up one or more HF paper pages by arXiv ID.")
    paper_parser.add_argument(
        "arxiv_id",
        nargs="+",
        help="One arXiv ID, multiple IDs, or a mistakenly quoted whitespace/comma-separated ID list.",
    )
    paper_parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to sleep between batch lookups and as the retry backoff base (default: 2.0).",
    )
    paper_parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries after the first lookup for 429/5xx/network errors (default: 3).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "paper":
        ids = _split_ids(args.arxiv_id)
        if not ids:
            raise ValueError("No arXiv IDs supplied.")
        if len(ids) == 1:
            print(json.dumps(paper(ids[0], retries=args.retries, delay=args.delay), ensure_ascii=False, indent=2))
            return 0

        results = []
        for index, arxiv_id in enumerate(ids):
            results.append(paper(arxiv_id, retries=args.retries, delay=args.delay))
            if index < len(ids) - 1:
                time.sleep(args.delay)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
