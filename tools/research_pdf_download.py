#!/usr/bin/env python3
"""Resolve or download PDFs for research-lit candidates.

The helper is deterministic: it never searches the web or asks a model to infer
missing files. For selected candidates it reuses existing local PDFs, downloads
from arXiv IDs, downloads explicit PDF URLs, or records why no PDF was fetched.
Successful local/downloaded PDFs are written back to ``local_pdf_path`` in
``search/candidate_metadata.json`` so later prepare steps can reuse them.

Examples
--------
python3 tools/research_pdf_download.py \
  --topic-name agent-interaction

python3 tools/research_pdf_download.py \
  --topic-name agent-interaction \
  --max-papers 20 \
  --no-download
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_fetch  # noqa: E402
import research_candidate_rank  # noqa: E402
from research_artifact_io import write_json  # noqa: E402
from research_survey_paths import add_survey_root_args, resolve_survey_root  # noqa: E402

MIN_PDF_BYTES = 10_240
PDF_HEADER = b"%PDF-"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_metadata(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidate metadata must be an object with a candidates list")
    seen: set[str] = set()
    for index, candidate in enumerate(payload["candidates"]):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate metadata record at index {index} is not an object")
        candidate_id = clean_text(candidate.get("id"))
        if not candidate_id:
            raise ValueError(f"candidate metadata record at index {index} is missing id")
        if candidate_id in seen:
            raise ValueError(f"duplicate candidate metadata id: {candidate_id}")
        seen.add(candidate_id)
    return payload


def resolve_existing_path(value: Any) -> Path | None:
    text = clean_text(value)
    if not text:
        return None
    path = Path(text)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, path]
    for item in candidates:
        if item.exists() and item.is_file():
            return item
    return None


def validate_pdf(path: Path, *, min_bytes: int) -> tuple[bool, str | None]:
    if not path.exists() or not path.is_file():
        return False, "file_not_found"
    size = path.stat().st_size
    if size < min_bytes:
        return False, f"pdf_too_small:{size}"
    with path.open("rb") as handle:
        header = handle.read(8)
    if not header.startswith(PDF_HEADER):
        return False, "not_a_pdf_header"
    return True, None


def candidate_pdf_url(candidate: dict[str, Any]) -> str | None:
    pdf_url = clean_text(candidate.get("pdf_url"))
    if pdf_url:
        return pdf_url

    open_access = candidate.get("openAccessPdf")
    if isinstance(open_access, dict):
        pdf_url = clean_text(open_access.get("url"))
        if pdf_url:
            return pdf_url

    semantic_scholar = candidate.get("semantic_scholar")
    if isinstance(semantic_scholar, dict):
        open_access = semantic_scholar.get("openAccessPdf")
        if isinstance(open_access, dict):
            pdf_url = clean_text(open_access.get("url"))
            if pdf_url:
                return pdf_url

    source_payloads = candidate.get("source_payloads")
    if isinstance(source_payloads, dict):
        s2_payload = source_payloads.get("semantic-scholar")
        if isinstance(s2_payload, dict):
            open_access = s2_payload.get("openAccessPdf")
            if isinstance(open_access, dict):
                return clean_text(open_access.get("url"))
    return None


def slug(value: str, *, max_length: int = 72) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    text = re.sub(r"-+", "-", text).strip("-._")
    return (text[:max_length].strip("-._") or "paper").lower()


def pdf_url_filename(candidate: dict[str, Any], url: str) -> str:
    candidate_id = clean_text(candidate.get("id")) or "candidate"
    title = clean_text(candidate.get("title")) or ""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{slug(candidate_id, max_length=24)}-{slug(title, max_length=64)}-{digest}.pdf"


def download_pdf_url(
    url: str,
    dest: Path,
    *,
    min_bytes: int,
    timeout: int,
) -> dict[str, Any]:
    if dest.exists():
        ok, reason = validate_pdf(dest, min_bytes=min_bytes)
        if ok:
            return {"path": str(dest), "size_bytes": dest.stat().st_size, "skipped": True}
        return {"path": str(dest), "error": f"existing_file_invalid:{reason}", "skipped": True}

    req = urllib.request.Request(url, headers={"User-Agent": arxiv_fetch._arxiv_user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"pdf_url_http_error:{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"pdf_url_download_failed:{exc}") from exc

    if len(data) < min_bytes:
        raise ValueError(f"downloaded_pdf_too_small:{len(data)}")
    if not data.startswith(PDF_HEADER):
        raise ValueError("downloaded_file_not_pdf")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return {"path": str(dest), "size_bytes": len(data), "skipped": False}


def selected_records(metadata: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    candidates = metadata["candidates"]
    if args.all_candidates:
        candidates = [candidate for candidate in candidates if candidate.get("verification_status") == "verified"
                      and research_candidate_rank.relevance_value(candidate) > 0]
        records = [
            {
                "rank": index,
                "selected": True,
                "id": candidate.get("id"),
                "title": candidate.get("title"),
            }
            for index, candidate in enumerate(candidates, start=1)
        ]
        return records, None

    ranking = research_candidate_rank.rank_candidates(
        candidates,
        citation_threshold=args.citation_threshold,
        upvote_threshold=args.upvote_threshold,
        max_selected=args.max_papers,
    )
    return [record for record in ranking["ranked_candidates"] if record.get("selected")], ranking


def resolve_or_download_pdf(
    candidate: dict[str, Any],
    *,
    downloaded_dir: Path,
    no_download: bool,
    min_bytes: int,
    timeout: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": candidate.get("id"),
        "title": candidate.get("title"),
        "status": None,
        "source": None,
        "path": None,
        "reason": None,
    }

    local = resolve_existing_path(candidate.get("local_pdf_path")) or resolve_existing_path(candidate.get("pdf_path"))
    if local is not None:
        ok, reason = validate_pdf(local, min_bytes=min_bytes)
        if ok:
            record.update(status="reused_local_pdf", source="local_pdf_path", path=str(local))
            candidate["local_pdf_path"] = str(local)
            return record
        record["reason"] = f"local_pdf_invalid:{reason}"

    arxiv_id = clean_text(candidate.get("arxiv_id"))
    if arxiv_id:
        if no_download:
            record.update(status="download_disabled", source="arxiv", reason="arxiv_id_available")
            return record
        try:
            result = arxiv_fetch.download(arxiv_id, output_dir=str(downloaded_dir))
            path = Path(result["path"])
            ok, reason = validate_pdf(path, min_bytes=min_bytes)
            if not ok:
                record.update(status="download_failed", source="arxiv", reason=reason)
                return record
            status = "reused_arxiv_pdf" if result.get("skipped") else "downloaded_arxiv_pdf"
            record.update(status=status, source="arxiv", path=str(path))
            candidate["local_pdf_path"] = str(path)
            if sleep_seconds > 0 and not result.get("skipped"):
                time.sleep(sleep_seconds)
            return record
        except Exception as exc:
            record.update(status="download_failed", source="arxiv", reason=str(exc))
            return record

    pdf_url = candidate_pdf_url(candidate)
    if pdf_url:
        if no_download:
            record.update(status="download_disabled", source="pdf_url", reason="pdf_url_available")
            return record
        dest = downloaded_dir / pdf_url_filename(candidate, pdf_url)
        try:
            result = download_pdf_url(pdf_url, dest, min_bytes=min_bytes, timeout=timeout)
            if result.get("error"):
                record.update(status="download_failed", source="pdf_url", reason=result["error"], path=result.get("path"))
                return record
            status = "reused_pdf_url" if result.get("skipped") else "downloaded_pdf_url"
            record.update(status=status, source="pdf_url", path=result["path"])
            candidate["local_pdf_path"] = result["path"]
            if sleep_seconds > 0 and not result.get("skipped"):
                time.sleep(sleep_seconds)
            return record
        except Exception as exc:
            record.update(status="download_failed", source="pdf_url", reason=str(exc))
            return record

    if clean_text(candidate.get("local_note_path")):
        record.update(status="skipped_local_note_only", source="local_note_path", reason="no_pdf_locator")
        return record

    if any(clean_text(candidate.get(key)) for key in ("doi", "url", "abs_url", "title")):
        record.update(status="pdf_locator_missing", reason="no_local_pdf_arxiv_or_pdf_url")
        return record

    record.update(status="insufficient_locator", reason="missing_title_and_pdf_locator")
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_survey_root_args(parser, survey_root_help="Survey root containing search/candidate_metadata.json.")
    parser.add_argument("--max-papers", type=int, default=20)
    parser.add_argument("--citation-threshold", type=int, default=50)
    parser.add_argument("--upvote-threshold", type=int, default=50)
    parser.add_argument("--all-candidates", action="store_true", help="Process every candidate instead of top ranked candidates.")
    parser.add_argument("--no-download", action="store_true", help="Only reuse/validate existing local PDFs and classify candidates.")
    parser.add_argument("--min-bytes", type=int, default=MIN_PDF_BYTES)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--sleep", type=float, default=1.0, help="Delay after successful network downloads.")
    parser.add_argument("--no-write-metadata", action="store_true", help="Do not update candidate_metadata.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_papers < 0:
        print("Error: --max-papers must be >= 0", file=sys.stderr)
        return 2
    if args.min_bytes < 0:
        print("Error: --min-bytes must be >= 0", file=sys.stderr)
        return 2

    try:
        survey_root = resolve_survey_root(args.survey_root, args.topic_name, repo_root=REPO_ROOT)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    search_dir = survey_root / "search"
    metadata_path = search_dir / "candidate_metadata.json"
    ranking_path = search_dir / "candidate_ranking.json"
    output_path = search_dir / "pdf_downloads.json"
    downloaded_dir = survey_root / "papers" / "downloaded"

    try:
        metadata = load_metadata(metadata_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        records, ranking = selected_records(metadata, args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if ranking is not None:
        ranking.update(
            {
                "metadata_path": str(metadata_path),
                "output_path": str(ranking_path),
                "survey_root": str(survey_root),
                "queries": metadata.get("queries"),
            }
        )
        write_json(ranking_path, ranking)

    candidates_by_id = {candidate.get("id"): candidate for candidate in metadata["candidates"]}
    download_records: list[dict[str, Any]] = []
    for record in records:
        candidate = candidates_by_id.get(record.get("id"))
        if not isinstance(candidate, dict):
            download_records.append(
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "status": "candidate_missing",
                    "source": None,
                    "path": None,
                    "reason": None,
                }
            )
            continue
        result = resolve_or_download_pdf(
            candidate,
            downloaded_dir=downloaded_dir,
            no_download=args.no_download,
            min_bytes=args.min_bytes,
            timeout=args.timeout,
            sleep_seconds=args.sleep,
        )
        result["rank"] = record.get("rank")
        download_records.append(result)

    success_statuses = {"reused_local_pdf", "reused_arxiv_pdf", "downloaded_arxiv_pdf", "reused_pdf_url", "downloaded_pdf_url"}
    counts: dict[str, int] = {}
    for item in download_records:
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1

    if not args.no_write_metadata:
        write_json(metadata_path, metadata)

    report = {
        "survey_root": str(survey_root),
        "metadata_path": str(metadata_path),
        "candidate_ranking": str(ranking_path) if ranking is not None else None,
        "processed_count": len(download_records),
        "updated_metadata": not args.no_write_metadata,
        "no_download": args.no_download,
        "success_count": sum(1 for item in download_records if item.get("status") in success_statuses),
        "counts": counts,
        "records": download_records,
    }
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "processed_count": report["processed_count"],
                "success_count": report["success_count"],
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
