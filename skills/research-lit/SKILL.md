---
name: research-lit
description: This skill should be used when the user asks to find papers, run a literature review, map related work, compare research directions, or research an academic topic. It confirms search queries, runs one unified candidate search, analyzes selected arXiv PDFs through paper-analysis note-only subagents, and builds source-grounded synthesis artifacts. Do not trigger for a casual fact question or one supplied paper unless broader discovery is requested.
argument-hint: [research-topic-and-constraints]
allowed-tools: Bash(*), Read, Write, Glob, Grep, WebSearch, WebFetch, Agent
---

# Research Literature Review

Research topic: $ARGUMENTS

## Execution Contract

This is an agent-facing controller. Deterministic scripts own retrieval, candidate identity checks and admission, metadata merging, ranking, PDF download, packets, projections, validation, and query-pack assembly. The controller owns query proposals, three-level relevance labels, subagent dispatch, source-grounded extraction, cross-paper normalization, relations, and review prose.

Run commands from the repository root. Use `uv run python tools/...` so the locked project runtime supplies PDF dependencies. When a tool exits successfully and its expected artifacts exist, treat those artifacts as its executable contract; inspect implementation only when the tool errors, artifacts are missing or inconsistent, or the user requests an implementation change. Read only the reference contract required by the current operation:

| When | Read |
|---|---|
| Candidate processing | `references/candidate-workflow.md` |
| Per-paper extraction | `references/paper-extraction.md`, `references/node-schema.md` |
| Canonical synthesis | `references/cross-paper-synthesis.md`, `references/node-schema.md` |
| Relations | `references/relation-data.md` |
| Inspect generated JSONL or projection failures | `references/jsonl-projections.md` |
| Note-only PDF analysis | `references/note-only-analysis.md`, then `../paper-analysis/SKILL.md` |

Never synthesize a paper from memory. Treat `search/candidate_papers.json` as the admitted downstream candidate pool; the search script owns identity checks and keeps detailed receipts in `search/verification_status.json`. Do not repeat or reinterpret that internal decision in later stages.

## Workspace And Reruns

Derive a concise filesystem-safe `<Topic-Name>` and create:

```text
database/<Topic-Name>/survey/
|-- search/
|-- papers/downloaded/
|-- notes/papers/
`-- synthesis/
    |-- packets/
    |-- extraction/
    |-- logic/
    |-- evidence/
    `-- validation/
projects/<Topic-Name>/survey -> ../../database/<Topic-Name>/survey
```

All originals live under `database/<Topic-Name>/survey/`; `projects/` contains only the survey symlink. On a same-topic rerun, execute the same ordered gates in the existing folder. Scripts preserve stable candidate IDs and reuse valid PDFs and notes; their receipts and `synthesis/manifest.json` determine whether later derived stages are reusable, invalid, or stale. Regenerate stale model-written stages and their downstream dependents; otherwise do not rewrite them.

## Workflow

### Stage 1: Research Preparation

#### 1.1 Create Survey Workspace

Derive `<Topic-Name>` in lowercase hyphen-case for English. For Chinese, preserve meaningful words and replace whitespace and punctuation with hyphens. Create the layout above and keep an existing correct survey symlink; if it points elsewhere, stop and report the conflict instead of replacing it. Pass `--topic-name "<Topic-Name>"` to normal tool calls; use `--survey-root` only for a custom location.

#### 1.2 Propose And Confirm Search Queries

Before search, produce `SEARCH_QUERIES`:

1. Use explicit user queries directly; otherwise propose 3–5 queries, default 4.
2. If the user provides a paragraph or multi-sentence description instead of explicit queries, first extract its main object, domain or population, mechanism or variable, task or outcome, and explicit constraints as keyword concepts; use those concepts to propose the queries. Do not pass the whole paragraph as one query. For a short topic phrase, keep its core terms as the anchor and expand only with close aliases or operational terms.
3. Make each query one retrieval hypothesis with 2–4 core terms or stable phrases. Include the main object, domain, or population as an anchor; for agent topics, examples include `agent`, `LLM agent`, `assistant`, `user`, or `human`.
4. Keep scope balanced: do not make a query broader than the user's topic or over-constrain it by stacking independent conditions.
5. Merge near-duplicates with `OR`. Construct queries from terms, stable quoted phrases such as `"multi turn"` or `"instruction following"`, whitespace or `AND` for required matches, and `OR` only for close synonyms.

For example, from the short topic `LLM reasoning length`, extract the anchors `LLM reasoning` and `length`, then add close operational concepts such as `chain-of-thought`, `output length`, or `token budget` to form query variants like `chain-of-thought length` and `reasoning token budget`.

Use these checks when refining queries:

| Query | Verdict | Reason |
|---|---|---|
| `agent OR agents user OR human interaction` | good | High-recall query with both agent and user/human anchors. |
| `LLM agent user feedback` | good | Focused mechanism query without excess constraints. |
| `interactive alignment` | too broad | Missing an agent/user anchor. |
| `agent interaction dynamic alignment instruction following preference learning` | too narrow | Too many independently required concepts. |
| `agent user interaction`; `agents human interaction` | redundant | Merge as `agent OR agents user OR human interaction`. |

Show the proposed queries as a numbered list together with scope, date, paper-type, and preprint constraints, then stop. Accept confirmation, editing one or more queries, deleting queries, adding queries, or replacing the entire list. Show the resulting list once as final `SEARCH_QUERIES`, and do not run database, Hugging Face, arXiv, Semantic Scholar, or web-fallback searches until the user confirms it. Pass one `--query` argument per confirmed query.

### Stage 2: Candidate Pool Processing

Read `references/candidate-workflow.md` before Stage 2.
Stage 2 builds and filters the candidate pool; PDF download and analysis begin in Stage 3.

#### 2.1 Unified Candidate Search

Run the sole search entrypoint:

```bash
uv run python tools/research_candidate_search.py \
  --query "QUERY_1" --query "QUERY_2" \
  --topic-name "<Topic-Name>" \
  --max-arxiv 20 --max-s2 20 \
  --hf-days 150 --hf-min-upvotes 50 \
  --year-from 2024 --year-to 2026 \
  --sleep 2.0
```

This command is the sole normal search entrypoint. It searches the local database, Hugging Face papers, arXiv, and Semantic Scholar; merges identities; builds relevance text; performs candidate identity checks internally; and writes the search receipts. Its `--sleep` value is the shared minimum outbound-request interval; apply the detailed retry and source-failure contract in `references/candidate-workflow.md`.

Use `--year-from` and `--year-to` for the confirmed publication range and omit both for any year. Keep `--hf-days` as the Hugging Face popularity-source freshness window rather than using it as a publication-range control.

Exit code 2 means no scriptable evidence; exit code 3 means candidates were found but none passed candidate admission. Either code stops Stage 2 and requires reporting the receipts.

#### 2.2 Evidence Gates

Read `search/source_status.json`, `search/search_summary.json`, and `search/verification_status.json`. Continue only when at least one source has `usable_candidate_count > 0` and `search/candidate_papers.json` is non-empty. Web fallback counts only when it yields concrete official paper metadata and passes the same scripted admission process.

#### 2.3 Pre-Relevance Enrichment

```bash
uv run python tools/research_candidate_enrich.py \
  --topic-name "<Topic-Name>" \
  --stage pre-relevance --sleep 0.25
```

The tool deduplicates and hydrates metadata without Semantic Scholar calls. It preserves search-stage admission fields and the complete audit pool while keeping `candidate_papers.json` aligned with the admitted candidates.

#### 2.4 Three-Level Relevance

For each record in `search/candidate_papers.json`, judge topical fit from `title` and the matching `relevanceText`/abstract using the compact rubric in `references/candidate-workflow.md`. Write only top-level numeric `relevance` in `candidate_metadata.json`; every admitted candidate must receive one value before citation enrichment.

#### 2.5 Citation Enrichment

```bash
uv run python tools/research_candidate_enrich.py \
  --topic-name "<Topic-Name>" \
  --stage citation --hf-upvote-threshold 50 --sleep 0.25
```

The script validates all admitted relevance labels, then enriches only admitted candidates with positive relevance under the Semantic Scholar failure contract in `references/candidate-workflow.md`.

### Stage 3: Local Processing And Synthesis

#### 3.1 Rank, Download, And Produce Paper Notes

```bash
uv run python tools/research_pdf_download.py \
  --topic-name "<Topic-Name>" --max-papers 20 \
  --citation-threshold 50 --upvote-threshold 50 \
  --min-bytes 10240 --timeout 60 --sleep 1.0
uv run python tools/research_note_tasks.py prepare --topic-name "<Topic-Name>"
```

The downloader applies the eligibility and ordering contract in `references/candidate-workflow.md`, then writes `search/candidate_ranking.json`, `search/pdf_downloads.json`, and local PDF paths.

Read `references/note-only-analysis.md`. For every pending task in `synthesis/paper_note_tasks.json`, dispatch a subagent with `../paper-analysis/SKILL.md`, `MODE=note-only`, and the task payload without reinterpretation. Dispatch at most configured `max_threads` concurrently and never more agents than pending tasks. Each subagent may create exactly its assigned `note.md`; other selected records remain eligible for packet evidence.

After all note subagents finish:

```bash
uv run python tools/research_note_tasks.py validate --topic-name "<Topic-Name>"
uv run python tools/research_evidence_prepare.py --topic-name "<Topic-Name>"
```

Stop if any required note is invalid. Evidence preparation writes `synthesis/packets/index.json` and one traceable source packet per selected paper.

Evidence preparation maps an available PDF, validated wiki-note route or configured trusted note root, or metadata to `fulltext`, `local-note`, or `metadata-only`, respectively. A present PDF with incomplete or empty page text fails full-text preparation without fallback because the default workflow has no OCR stage.

#### 3.2 Build Scaffolds And Extract Scoped Nodes

```bash
uv run python tools/research_synthesis_nodes.py --topic-name "<Topic-Name>"
```

Before filling each `synthesis/extraction/<paper-id>.md`, read `references/paper-extraction.md` and `references/node-schema.md`. For a full-text PDF packet, first list detected sections:

```bash
uv run python tools/research_pdf_section_extract.py \
  --topic-name "<Topic-Name>" \
  --paper-id "<paper-id>" \
  --list-sections
```

Then extract each selected section by its exact detected title:

```bash
uv run python tools/research_pdf_section_extract.py \
  --topic-name "<Topic-Name>" \
  --paper-id "<paper-id>" \
  --section "<exact-section-title>"
```

Use exact detected titles to extract the functionally relevant sections defined in `references/paper-extraction.md`. For `local-note` or `metadata-only`, read only the packet. Fill the scoped Evidence, Claim, Problem, and Heuristic schemas without exceeding the packet's evidence level.

```bash
uv run python tools/research_synthesis_compile.py extraction --topic-name "<Topic-Name>"
```

Continue only when extraction validation is valid. This gate writes Evidence Markdown/JSONL and the candidate index.

#### 3.3 Compare Candidates And Write Canonical Domain Logic

Read `references/cross-paper-synthesis.md` and `references/node-schema.md`. Compare every candidate node by kind, merge only semantically compatible domain-level nodes, preserve narrower/opposing statements, and account for every candidate in `synthesis/extraction/clusters.md`. Write canonical nodes to `synthesis/logic/logic.md`.

```bash
uv run python tools/research_synthesis_compile.py canonical --topic-name "<Topic-Name>"
```

The gate projects claims, problems, and heuristics JSONL. Continue only when valid.

#### 3.4 Generate Relation Data

Read `references/relation-data.md`, reopen the relevant source sections, and write only source-grounded relations to `synthesis/logic/relations.md`. An empty relation set is valid.

```bash
uv run python tools/research_synthesis_compile.py relations --topic-name "<Topic-Name>"
uv run python tools/research_synthesis_compile.py check --topic-name "<Topic-Name>"
```

Continue only when the final check reports `valid: true`.

#### 3.5 Produce Literature Review

Use candidate metadata/ranking, extraction files, packet routing, canonical Logic/Evidence, and relations. Write a paper table with `Paper`, `Venue`, `Method`, `Key Result`, `Relevance to Us`, `Evidence Level`, and `Source`, followed by 3–5 substantive landscape paragraphs covering themes, consensus, disagreements, open problems, and implications. Distinguish arXiv preprints from confirmed venues. Do not include candidates lacking completed deep extraction.

#### 3.6 Save And Validate Literature Review

Save the report to `notes/literature_review.md`, then run:

```bash
uv run python tools/research_synthesis_compile.py review --topic-name "<Topic-Name>"
```

Fix it until valid and return the same review to the user.

#### 3.7 Build And Check Query Pack

```bash
uv run python tools/research_query_pack_build.py --topic-name "<Topic-Name>" --max-chars 8000
uv run python tools/research_query_pack_build.py --topic-name "<Topic-Name>" --check
```

The builder is the sole query-pack writer and assembles compact context from queries, paper/evidence JSONL, canonical Logic, relations, the required literature review, and the validated `wiki_notes.json` route index.

## Completion Gate

Completion requires: a non-empty admitted candidate pool; valid paper-note tasks; packet, extraction, canonical, relation, and review validations; `notes/literature_review.md`; `synthesis/wiki_notes.json`; and a current `synthesis/query_pack.md`. Report partial completion explicitly if any gate stops the workflow.
