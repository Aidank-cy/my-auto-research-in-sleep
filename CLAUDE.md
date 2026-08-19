# Project: my-auto-research-in-sleep

## Pipeline Status

```yaml
stage: literature-intake
idea: ""
current_branch: "main"
baseline: ""
training_status: idle
language: zh
active_skills:
  - research-lit
  - paper-analysis
  - landscape-synthesis
last_updated: "2026-07-14"
```

## Project Constraints

- Adapt ARIS one stage at a time.
- Keep outputs Chinese-first unless a downstream academic artifact requires English.
- Do not run GPU experiments during the literature-intake phase.
- Do not auto-submit or auto-write papers during the initial localization phase.

## Non-Goals

- Do not copy the full ARIS skill corpus yet.
- Do not modify idea generation, experiment deployment, or paper writing until literature intake is stable.

## Paper Library

- Durable output (per-paper `note.md`/`note.json`/`images/`, per-topic `overview.md`) lives in the Obsidian vault, outside this repo.
- `.aris/verify-papers/<run-id>/` holds only ephemeral per-run cache (candidate/verified/score manifests, one subfolder per timestamped run) — safe to wipe between runs.
- Image filenames under `images/` are figure-extractor's own output (`{arxiv_id}-Figure{N}-1.png` / `{arxiv_id}-Table{N}-1.png`), referenced in place by `note.md`/`note.json` — never copied or renamed to a separate "friendly" name, to avoid duplicate files holding the same image.

## Search Scope Control

- `research-lit`'s Step 1.5 always asks a fixed 5-question set (subfield, time range, paper type, keywords/exclusions, venue/quality) before any external search, and uses the answers (`REFINED_QUERY`/`CONSTRAINTS`) instead of the raw topic string for both the scope probe and the actual fetch calls.
- Step 2.5 adaptively caps the combined arXiv + Semantic Scholar fetch to `SEARCH_RESULT_TARGET` (200–500 papers pre-dedup) — narrow topics are never padded up to the floor, only broad topics are capped down to the ceiling.

## Research Wiki

- Optional: `research-lit` Step 6 ingests top-scored papers into a local `research-wiki/` only if that directory already exists. Not initialized by default in this project.

## Compute Budget

- Literature-intake phase: local CPU only.
- GPU budget: not configured yet.
