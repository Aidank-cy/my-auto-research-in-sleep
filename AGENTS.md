# AGENTS.md

## Language

Agent communication, progress updates, planning, commits, and changelogs must be in English.

Project-facing research outputs may be written in Chinese when the local pipeline or prompt requests Chinese output.

## Project Goal

This repository is a local, project-specific research automation pipeline: literature intake (`research-lit`) → per-paper extraction (`paper-analysis`) → field-level synthesis (`landscape-synthesis`). Durable output lives in the Obsidian vault, external to this repo; `.aris/` holds only ephemeral per-run cache.

## Adaptation Rules

- Prefer changing `skills/*/SKILL.md` before changing helper code in `tools/`.
- When helper code is changed, add or run focused tests where practical.
- Do not commit `.aris/`, `.claude/`, `.env`, downloaded papers, or generated research outputs unless explicitly requested.

## Current Scope

```text
skills/research-lit          — discover, verify, scope-filter, dispatch, output table
skills/paper-analysis        — single-paper extraction (dispatched by research-lit)
skills/landscape-synthesis   — field-level synthesis (dispatched by research-lit)
skills/idea-creator          — copied upstream baseline for local idea-generation adaptation
```

Any additional stage (idea generation, novelty check, refinement, writing, etc.) is out of scope until explicitly added back.
