#!/usr/bin/env python3
"""
relevance_score.py — Relevance scoring and score-patching helper.

The former score-based research workflow used this script in two phases. Its
selection step ran `score` once over the
whole candidate set before PDF download/analysis, so ranking and selection use
only metadata, references, HF freshness, and abstract judgments. A later phase
runs `patch` over that already-scored JSON after paper-analysis has created
note files. The script:

  1. Combines three signals into one relevance_score.total per paper — all
     citation volume comes from *within this run's own candidate set*, never
     from an external citation-count metadata field:
       - internal citation graph: given each paper's own raw, unfiltered
         reference list (from Semantic Scholar's /paper/{id}/references
         endpoint — see semantic_scholar_fetch.py's `references` command,
         normally captured after verified topical admission and passed inline, or
         saved by the caller to <paper_folder>/references.json and loaded
         from there by this script), the script normalizes each reference's
         external ID (arXiv/DOI, stripping prefixes and arXiv version
         suffixes), matches it against this manifest's own paper_ids, and
         builds the reverse graph itself (how many *other* papers in the set
         cite *this* one), min-max normalized across the set. The caller
         never does file loading, ID matching, or graph reversal by hand.
       - Hugging Face freshness: days since the paper was last featured on
         HF Daily Papers (`hf_submitted_on_daily_at`, from
         huggingface_fetch.py's `paper` command — see
         the compatibility scoring manifest), scoped to a recent window
         (--freshness-window-days, default 90 / ~3 months): within the
         window, more recent = higher, min-max normalized across the set.
         A paper never featured on HF Daily Papers, or featured longer ago
         than the window, gets the neutral/median value instead — both are
         "no current signal," not a penalty.
       - topical_relevance and novelty, two independent 0.0-1.0 judgments
         supplied by the caller (the LLM executor judges these against a fixed
         rubric supplied by the compatibility caller — a script cannot
         make a semantic judgment). These two are first combined into a single
         abstract_relevance value via --abstract-weights, then that combined
         value is what enters the outer total alongside the two signals above.

     Fairness reweighting: a paper with no real HF signal at all (never
     featured, or featured outside --freshness-window-days) would otherwise
     have hf_freshness silently fall back to the set's neutral/median value —
     that's a filler, not a measurement of this specific paper. Such a paper
     drops the hf_freshness term entirely and has internal_citation_graph's
     and abstract_relevance's weights renormalized to still sum to 1, so
     every paper's total stays comparable on the same 0-1 scale regardless of
     which signals were actually available for it. relevance_score records
     both hf_freshness_available (bool) and the effective per-paper weights
     actually used (weights) alongside the nominal --weights as configured
     (nominal_weights), so this is auditable per paper, not silent.
  2. The separate `patch` command patches relevance_score, published_date,
     venue, and discovery_source into each paper's note.json and a one-line
     "*Relevance score: ...*" note into note.md, directly under the
     existing "*Verification: ...*" line written by the paper-analysis
     skill. Re-running is idempotent — it replaces rather than duplicates
     that key/line.
  3. Prints every paper's relevance_score, sorted by total descending, so the
     caller can use it directly for compatibility selection.

Retained for standalone and legacy artifact compatibility. The current
three-stage `research-lit` controller uses binary relevance plus
`research_candidate_rank.py` and does not invoke this helper.

Called directly at its fixed repo path (`tools/relevance_score.py`) — this
project has one canonical tools/ location, so there is no dynamic resolution
chain to walk.

CLI:

  python3 tools/relevance_score.py score --manifest manifest.json \
      [--weights 0.4,0.3,0.3] [--abstract-weights 0.5,0.5]

  python3 tools/relevance_score.py patch --scores scored.json

  --weights are (internal_citation_graph, hf_freshness, abstract_relevance),
  in that order.

Manifest schema (manifest.json), one entry per paper:

  [
    {
      "paper_id": "2307.03172",
      "paper_folder": "/abs/path/to/topic-folder/paper-folder",
      "published_date": "2023-07-06",
      "venue": "NeurIPS 2023",
      "discovery_source": "semantic_scholar",
      "topical_relevance": 0.9,
      "novelty": 0.5,
      "hf_submitted_on_daily_at": "2026-07-10"
    }
  ]

- published_date / venue: null if no Semantic Scholar match. Not used in any
  score computation — venue is patched straight through to note.json
  (legacy overview generation reads it from there, since verified_papers.json
  carries no venue field).
- discovery_source: which source (zotero/obsidian/semantic_scholar/arxiv) first
  surfaced this paper in the legacy search stage — known only in the
  orchestrator's own context at that point, with no file it's otherwise
  written to (verified_papers.json's PaperInput schema has no room for it
  either). Also not used in scoring; patched straight through to
  note.json for the same reason as venue. null if not tracked.
- hf_submitted_on_daily_at: the ISO date (YYYY-MM-DD) this paper was last
  featured on Hugging Face Daily Papers, from huggingface_fetch.py's `paper`
  command's `submitted_on_daily_at` field. `null` if never featured — treated
  as the least-fresh paper in the set, not neutral/missing data.
- references (optional, not shown above): if present, an explicit raw
  *unfiltered* list of external IDs (arXiv ID or DOI, whichever
  `externalIds` has) this paper cites, and takes priority over the on-disk
  file below — useful for testing/manual overrides. Normally omitted: this
  script instead loads <paper_folder>/references.json (the raw
  output of `semantic_scholar_fetch.py references <paper_id>`, saved there
  by the caller beforehand) automatically. Either way, do not filter or
  match the list yourself; [] if neither is available. This script
  normalizes each ID (strips "arXiv:"/"ARXIV:" prefixes and arXiv version
  suffixes like "v2", lowercases DOIs) and matches against this manifest's
  own paper_ids, then builds the reverse graph (who-cites-me) and does the
  counting — the caller does not do file loading, ID matching, or graph
  reversal by hand.
- topical_relevance / novelty: required, one of the fixed values 0.1/0.3/0.5/
  0.7/0.9 each, judged beforehand against the compatibility caller's rubric.
  This script itself doesn't enforce
  the five-value set — it accepts any float in [0, 1] — the discreteness is
  a rubric-following convention for the caller, not a script-side constraint.
  Combined into abstract_relevance via --abstract-weights (default 0.5/0.5).

`score` is computation-only and may run before paper folders exist. For
`patch`, the input is the already-scored JSON emitted by `score`, with
`paper_folder` populated; no relevance math is recomputed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

_VERIFICATION_LINE_RE = re.compile(r"^\*Verification:.*\*[ \t]*$", re.MULTILINE)
_SCORE_LINE_RE = re.compile(r"^\*Relevance score:.*\*[ \t]*$", re.MULTILINE)
_ARXIV_PREFIX_RE = re.compile(r"^(arxiv|arXiv|ARXIV):", re.IGNORECASE)
_ARXIV_URL_PREFIX_RE = re.compile(r"^https?://arxiv\.org/abs/", re.IGNORECASE)
_ARXIV_VERSION_SUFFIX_RE = re.compile(r"v\d+$", re.IGNORECASE)


def _normalize_ref_id(ref: str) -> str:
    """Normalize an external ID (arXiv ID or DOI) for cross-source matching:
    strip an "arXiv:"/"ARXIV:" prefix or an arxiv.org/abs/ URL prefix, strip a
    trailing arXiv version suffix ("v2"), and lowercase (DOIs are
    case-insensitive; arXiv IDs are already lowercase/numeric)."""
    ref = ref.strip()
    ref = _ARXIV_URL_PREFIX_RE.sub("", ref)
    ref = _ARXIV_PREFIX_RE.sub("", ref)
    ref = _ARXIV_VERSION_SUFFIX_RE.sub("", ref)
    return ref.lower()


def _load_references_from_disk(paper_folder: str | None) -> list[str]:
    """Load raw external IDs from <paper_folder>/references.json —
    the verbatim output of `semantic_scholar_fetch.py references <paper_id>`,
    saved there by the compatibility caller. Returns [] if paper_folder
    is missing, the file doesn't exist, or it's unparseable — this paper just
    won't contribute internal-citation-graph signal for/from it."""
    if not paper_folder:
        return []
    ref_path = Path(paper_folder) / "references.json"
    if not ref_path.exists():
        return []
    try:
        entries = json.loads(ref_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(entries, dict):
        refs = entries.get("references")
        if isinstance(refs, list):
            return [str(ref) for ref in refs if ref]
        entries = entries.get("data") or []
    if not isinstance(entries, list):
        return []
    raw_ids = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        external_ids = (entry or {}).get("externalIds") or {}
        ref_id = external_ids.get("ArXiv") or external_ids.get("DOI")
        if ref_id:
            raw_ids.append(str(ref_id))
    return raw_ids


def _freshness_raw(submitted_on_daily_at: str | None, today: date, window_days: int) -> float | None:
    """Raw freshness measure, scoped to a recent window (default 3 months /
    ~90 days): `window_days - days_since_featured`, so more recent = larger
    = better after min-max normalization. Returns `None` (not a low-end
    sentinel) when the paper was never featured, or was featured but longer
    ago than `window_days` — both cases mean "no current signal," which
    `_min_max_normalize` already treats as neutral/median, same as any other
    missing value in this script. A paper featured 8 months ago isn't
    meaningfully "fresh" anymore either, so it gets the same neutral
    treatment as one never featured at all, rather than being penalized
    below papers with no HF page whatsoever."""
    if not submitted_on_daily_at:
        return None
    try:
        featured = datetime.strptime(submitted_on_daily_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    days = (today - featured).days
    if days > window_days:
        return None
    return float(window_days - days)


def _min_max_normalize(values: dict[str, float | None]) -> dict[str, float]:
    """Min-max normalize to [0, 1]; entries with None get the neutral (median)
    of the known values rather than 0, so missing data isn't penalized as
    irrelevance. If every value is None, or all known values are equal,
    everyone gets 0.5 (no signal to differentiate on). Callers that need
    "missing = worst" instead of "missing = neutral" (e.g. HF freshness)
    pass an explicit sentinel raw value rather than None — this function
    itself never treats a real number as anything but a real number."""
    known = sorted(v for v in values.values() if v is not None)
    if not known:
        return {k: 0.5 for k in values}

    mid = known[len(known) // 2] if len(known) % 2 == 1 else (
        known[len(known) // 2 - 1] + known[len(known) // 2]
    ) / 2
    lo, hi = known[0], known[-1]

    def _scale(v: float) -> float:
        if hi == lo:
            return 0.5
        return (v - lo) / (hi - lo)

    return {k: (_scale(v) if v is not None else _scale(mid)) for k, v in values.items()}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_scores(
    manifest: list[dict],
    weights: tuple[float, float, float],
    abstract_weights: tuple[float, float],
    freshness_window_days: int = 90,
) -> list[dict]:
    w_internal, w_freshness, w_abstract = weights
    w_relevance, w_novelty = abstract_weights
    today = date.today()

    valid_ids = {entry["paper_id"] for entry in manifest}
    # Map normalized ID -> original paper_id, so raw references (which may be
    # "arXiv:2401.00001v2", a bare arXiv ID, or a DOI in any casing) can be
    # matched against this manifest's own paper_ids regardless of formatting.
    normalized_to_pid = {_normalize_ref_id(pid): pid for pid in valid_ids}

    freshness_raw: dict[str, float | None] = {}
    cited_by: dict[str, list[str]] = {pid: [] for pid in valid_ids}
    for entry in manifest:
        pid = entry["paper_id"]
        freshness_raw[pid] = _freshness_raw(entry.get("hf_submitted_on_daily_at"), today, freshness_window_days)
        # Reverse this paper's raw, unfiltered reference list: normalize each
        # reference ID, check whether it matches another paper_id in this same
        # manifest, and if so append pid to that target's cited_by list. This
        # is the mechanical file-loading + ID-matching + graph-reversal step —
        # the caller never does any of this by hand. An explicit "references"
        # in the manifest entry wins; otherwise load from
        # <paper_folder>/references.json (compatibility reference output).
        references = entry.get("references")
        if references is None:
            references = _load_references_from_disk(entry.get("paper_folder"))
        seen_targets_for_this_paper: set[str] = set()
        for ref in references or []:
            target = normalized_to_pid.get(_normalize_ref_id(ref))
            if target and target != pid and target not in seen_targets_for_this_paper:
                cited_by[target].append(pid)
                seen_targets_for_this_paper.add(target)

    internal_raw = {pid: float(len(citers)) for pid, citers in cited_by.items()}

    freshness_score = _min_max_normalize(freshness_raw)
    internal_score = _min_max_normalize(internal_raw)

    results = []
    for entry in manifest:
        pid = entry["paper_id"]
        topical_relevance = entry.get("topical_relevance")
        novelty = entry.get("novelty")
        if topical_relevance is None:
            raise ValueError(f"paper_id {pid!r}: topical_relevance is required (0.0-1.0)")
        if novelty is None:
            raise ValueError(f"paper_id {pid!r}: novelty is required (0.0-1.0)")
        topical_relevance = _clamp01(float(topical_relevance))
        novelty = _clamp01(float(novelty))
        abstract_relevance = w_relevance * topical_relevance + w_novelty * novelty

        f_score = freshness_score[pid]
        i_score = internal_score[pid]

        # Fairness reweighting: a paper with no real HF signal (never featured,
        # or featured outside the recency window) has freshness_raw == None —
        # f_score for it is only ever the set's neutral fallback (see
        # _min_max_normalize), which is not a real measurement of *this*
        # paper. Folding w_freshness * 0.5-ish into its total anyway would
        # silently reward/penalize it based on an arbitrary neutral filler
        # rather than an absent signal. Instead, drop the freshness term
        # entirely for this paper and renormalize the other two weights to
        # still sum to 1, so every paper's total is comparable on the same
        # 0-1 scale regardless of which signals were actually available for it.
        has_freshness = freshness_raw[pid] is not None
        if has_freshness:
            w_internal_eff, w_freshness_eff, w_abstract_eff = w_internal, w_freshness, w_abstract
        else:
            remaining = w_internal + w_abstract
            if remaining > 0:
                w_internal_eff = w_internal / remaining
                w_abstract_eff = w_abstract / remaining
            else:
                w_internal_eff, w_abstract_eff = w_internal, w_abstract
            w_freshness_eff = 0.0

        total = w_internal_eff * i_score + w_freshness_eff * f_score + w_abstract_eff * abstract_relevance

        results.append(
            {
                "paper_id": pid,
                "paper_folder": entry.get("paper_folder"),
                "published_date": entry.get("published_date"),
                "venue": entry.get("venue"),
                "discovery_source": entry.get("discovery_source"),
                "relevance_score": {
                    "total": round(total, 4),
                    "internal_citation_graph": round(i_score, 4),
                    "cited_by_within_set": sorted(cited_by[pid]),
                    "hf_freshness": round(f_score, 4),
                    "hf_freshness_available": has_freshness,
                    "hf_submitted_on_daily_at": entry.get("hf_submitted_on_daily_at"),
                    "topical_relevance": round(topical_relevance, 4),
                    "novelty": round(novelty, 4),
                    "abstract_relevance": round(abstract_relevance, 4),
                    "weights": {
                        "internal_citation_graph": round(w_internal_eff, 4),
                        "hf_freshness": round(w_freshness_eff, 4),
                        "abstract_relevance": round(w_abstract_eff, 4),
                    },
                    "nominal_weights": {
                        "internal_citation_graph": w_internal,
                        "hf_freshness": w_freshness,
                        "abstract_relevance": w_abstract,
                    },
                    "abstract_weights": {
                        "relevance": w_relevance,
                        "novelty": w_novelty,
                    },
                },
            }
        )

    results.sort(key=lambda r: r["relevance_score"]["total"], reverse=True)
    return results


def _format_score_line(relevance_score: dict) -> str:
    return (
        f"*Relevance score: {relevance_score['total']:.2f} "
        f"(internal citations {relevance_score['internal_citation_graph']:.2f} · "
        f"HF freshness {relevance_score['hf_freshness']:.2f} · "
        f"topical relevance {relevance_score['topical_relevance']:.2f} · "
        f"novelty {relevance_score['novelty']:.2f})*"
    )


def patch_note_json(
    paper_folder: Path,
    relevance_score: dict,
    published_date: str | None,
    venue: str | None,
    discovery_source: str | None,
) -> None:
    json_path = paper_folder / "note.json"
    if not json_path.exists():
        print(f"WARN: {json_path} not found, skipping json patch.", file=sys.stderr)
        return
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["relevance_score"] = relevance_score
    # published_date / venue / discovery_source aren't part of paper-analysis's
    # own note.json schema, but compatibility consumers need each per paper from
    # note.json alone: landscape-synthesis's momentum-per-cluster
    # needs published_date, while legacy overview output needs
    # venue and discovery_source (verified_papers.json's PaperInput dataclass
    # has no room for any of the three — id/arxiv_id/doi/title only). This
    # script already has all three in the manifest for the citation-rate
    # calc / S2 lookup, so it persists them here too.
    data["published_date"] = published_date
    data["venue"] = venue
    data["discovery_source"] = discovery_source
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_note_md(paper_folder: Path, relevance_score: dict) -> None:
    md_path = paper_folder / "note.md"
    if not md_path.exists():
        print(f"WARN: {md_path} not found, skipping markdown patch.", file=sys.stderr)
        return
    text = md_path.read_text(encoding="utf-8")
    score_line = _format_score_line(relevance_score)

    if _SCORE_LINE_RE.search(text):
        text = _SCORE_LINE_RE.sub(score_line, text, count=1)
    elif _VERIFICATION_LINE_RE.search(text):
        text = _VERIFICATION_LINE_RE.sub(lambda m: f"{m.group(0)}\n{score_line}", text, count=1)
    else:
        # Fallback: no verification line found (shouldn't normally happen) —
        # insert right after the first heading line.
        lines = text.splitlines()
        insert_at = 1 if lines else 0
        lines.insert(insert_at, score_line)
        text = "\n".join(lines) + "\n"

    md_path.write_text(text, encoding="utf-8")


def score_command(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    weights = tuple(float(w) for w in args.weights.split(","))
    if len(weights) != 3:
        raise ValueError("--weights must have exactly 3 comma-separated values")
    abstract_weights = tuple(float(w) for w in args.abstract_weights.split(","))
    if len(abstract_weights) != 2:
        raise ValueError("--abstract-weights must have exactly 2 comma-separated values")

    results = compute_scores(manifest, weights, abstract_weights, args.freshness_window_days)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def patch_command(args: argparse.Namespace) -> int:
    """Patch note.json/note.md from an already-scored result list.

    This is intentionally separate from `score`: a compatibility workflow may
    compute values before PDFs are downloaded. Once paper-analysis creates
    note files, patching only needs to attach those saved
    values to disk artifacts, not recalculate graph/freshness math.
    """
    results = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    patched = []
    for result in results:
        folder = result.get("paper_folder")
        if not folder:
            print(f"WARN: paper_id {result.get('paper_id')!r} has no paper_folder, skipping file patch.", file=sys.stderr)
            continue
        relevance_score = result.get("relevance_score")
        if not relevance_score:
            print(f"WARN: paper_id {result.get('paper_id')!r} has no relevance_score, skipping file patch.", file=sys.stderr)
            continue
        paper_folder = Path(folder)
        patch_note_json(
            paper_folder,
            relevance_score,
            result.get("published_date"),
            result.get("venue"),
            result.get("discovery_source"),
        )
        patch_note_md(paper_folder, relevance_score)
        patched.append(result.get("paper_id"))

    print(json.dumps({"patched": patched, "count": len(patched)}, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score and patch relevance scores into note.json/note.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_parser = subparsers.add_parser("score", help="Compute relevance scores without mutating paper notes")
    score_parser.add_argument("--manifest", required=True, help="Path to the manifest JSON (see module docstring).")
    score_parser.add_argument(
        "--weights",
        default="0.4,0.3,0.3",
        metavar="INTERNAL,FRESHNESS,ABSTRACT",
        help="Comma-separated weights for internal_citation_graph,hf_freshness,abstract_relevance (default: 0.4,0.3,0.3).",
    )
    score_parser.add_argument(
        "--abstract-weights",
        default="0.5,0.5",
        metavar="RELEVANCE,NOVELTY",
        help="Comma-separated weights for topical_relevance,novelty when combining into abstract_relevance (default: 0.5,0.5).",
    )
    score_parser.add_argument(
        "--freshness-window-days",
        type=int,
        default=90,
        help="Recency window (default: 90, ~3 months) for hf_freshness. A paper featured on HF Daily "
             "Papers within this many days of today gets a real recency gradient; never featured, or "
             "featured longer ago than this, both get the neutral/median value instead.",
    )

    patch_parser = subparsers.add_parser("patch", help="Patch notes from already-computed score results")
    patch_parser.add_argument("--scores", required=True, help="Path to scored JSON emitted by the score command.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "score":
        return score_command(args)
    if args.command == "patch":
        return patch_command(args)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
