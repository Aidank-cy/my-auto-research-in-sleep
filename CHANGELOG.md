# Changelog

## [Unreleased]

### Removed

- Remove the deprecated `--verify-delay` candidate-search alias so `--sleep` is the single global request-delay setting.

### Changed

- Keep runtime databases, project result links, tool caches, and portable research bundles out of source-control publications.
- Reorganize research artifacts into topic-first `database/<topic>/survey` roots, update survey path resolution and project symlinks, and migrate existing path-bearing receipts without rewriting paper notes or canonical research content.
- Consolidate research-lit Evidence and Logic formats into one shared node schema, reduce duplicated controller policy, and classify generated JSONL guidance as an on-demand reference without changing workflow behavior.
- Distinguish current three-stage research-lit tools from retained standalone/legacy score, reference, and wiki helpers, remove obsolete step references, and document stable same-topic candidate IDs in the rerun contract.
- Clarify Stage 1 query generation: extract keyword concepts from paragraph-length research requests before proposing query variants, and document the `LLM reasoning length` expansion example.
- Replace redundant or dangling research-lit prohibitions with single positive ownership, stage, schema, retry, and artifact rules while preserving safety-critical gates.
- Align the note-only dispatch contract by carrying `discovery_source` in generated tasks and limiting `paper_type` persistence to full-mode `note.json` output.
- Complete the current research-lit execution contract with portable-derived topic naming, script trust, source-failure handling, Stage 2 boundaries, packet fallback levels, OCR limits, exact section-reader commands, manifest freshness, and bounded note-task retry rules.
- Align current research-lit query proposal and confirmation rules with the portable workflow, including default query count, topic anchors, scope balancing, query syntax, duplicate merging, examples, and edited-list handling.
- Make the search-confirmation pause explicitly accept confirming, editing, deleting, adding, or fully replacing the numbered query list before retrieval begins.
- Define a compact three-level relevance rubric based on topical object/outcome match, centrality, substantive partial value, and insufficient or incidental evidence.
- Treat paper identity checking as an internal candidate-search responsibility instead of foregrounding it throughout the agent-facing research workflow.
- Configure the root project as a Python 3.12 `uv` workspace with a reproducible `.venv`, locked runtime/test dependencies, and project-local pytest import paths.
- Replace the verification-only delay in candidate search with one `--sleep` setting that paces Hugging Face, arXiv, Semantic Scholar, and verification requests across source boundaries and retries.
- Rebuild the current `research-lit` skill around the portable three-stage workflow while retaining a single root `research_candidate_search.py` entrypoint and leaving the portable bundle unchanged.
- Add explicit `paper-analysis` note-only mode so arXiv PDF subagents create exactly one analytical `note.md`, with unresolved figure/table placeholders preserved as text.
- Replace the former reference-coverage/internal-graph selection contract with verified-only three-level relevance, citation-aware ranking, source packets, canonical synthesis, literature review, note registration, and query-pack gates.
- Assign deterministic retrieval, source accounting, relevance-schema validation, selection, freshness, route, relation, and review-coverage checks to scripts while retaining topical judgments, paper interpretation, and cross-paper synthesis as Agent work.

### Added

- Add the upstream `idea-creator` skill as an unchanged baseline for local adaptation.
- Integrate arXiv/Crossref/Semantic Scholar paper verification, publication-year filtering, detailed verification receipts, and same-topic field reuse into unified candidate search.
- Add deterministic arXiv note-task preparation and validation, stable `wiki_notes.json` routing, locked `pypdf` runtime dependencies, and end-to-end contract tests.

### Fixed

- Make the standalone Hugging Face paper lookup perform an initial request when retries are zero and treat a null API payload as an absent community signal instead of crashing.
- Keep prior paper IDs stable when same-topic searches discover new candidates, append new IDs after the existing range, and keep current-run source counts separate from historical source membership.
- Preserve verified arXiv note tasks when a same-topic rerun reuses an existing local PDF, and report the active custom survey root in regenerated ranking receipts.
- Include the HTTP client required by the figure extractor in the locked UV runtime and cover its remote extraction/download path with a functional test.
- Retry each Semantic Scholar search query up to 15 times at a fixed two-second interval, while respecting a longer server-provided `Retry-After` value; preserve Hugging Face publication dates when constructing candidates so year-range filtering does not discard them.
- Centralize citation-enrichment Semantic Scholar lookups behind a configurable 15-retry, two-second wrapper shared by title and identifier fetches.
- Preserve verified-only candidate projections after deduplication and exclude unverified or irrelevant candidates from all ranking and PDF paths, including `--all-candidates`.
- Keep current-run source receipts separate from historical candidate source membership, overwrite skipped-source raw artifacts, and preserve relevance/PDF fields on same-topic reruns.
- Validate paper-note section order and emit `note_path` so query packs contain usable note routes.
- Preserve current-run source counts during deduplication while reporting historical source membership separately.
- Require numeric relevance values `0`, `0.5`, or `1` for admitted candidates; rank verified positive-relevance papers with direct matches before partial matches and create note tasks only for selected successful arXiv PDFs.
- Revalidate note-only structure and PDF/identity input signatures on rerun, require validated wiki-note routes for packets and query packs, and verify literature-review table coverage against deeply extracted papers.
- Treat `idea:*` and `exp:*` relation nodes as externally owned for existence checks until their schemas are integrated, without weakening relation direction or provenance validation.
- Make note status transitions executable: validation now promotes repaired notes to reusable, keeps invalid or unchanged stale notes pending, and blocks packet preparation unless note receipts and wiki routes are fully valid.

- Serialize local Semantic Scholar requests and share 429 cooldowns across search and reference subprocesses, with actionable diagnostics when no API key is configured.
- Pace unauthenticated Semantic Scholar traffic at one request every two seconds for the shared public pool.
- Paginate Semantic Scholar relevance searches within the API page limit while surfacing 429/5xx responses after retries without fallback.
- Preserve arXiv/DOI external identifiers when normalizing Semantic Scholar references for internal citation matching.
- Extend the shared cooldown even when a terminal Semantic Scholar 429 has exhausted retries, while preserving no-fallback behavior for 429/5xx responses.
- Distinguish successful empty reference lists from API failures, resume only missing/retryable fetches, pause scoring below 95% reference coverage, and reuse pre-score caches in Step 6.
- Align the research-lit Semantic Scholar quality filter with the CLI's actual `--min-citations` option.
- Normalize arXiv-only venues as `arXiv preprint`, upgrading them only when the arXiv comment explicitly states acceptance at a formal venue.
- Seed same-topic reruns from existing reference artifacts, gate the complete old-plus-new scoring graph, and migrate legacy null arXiv venues during score refresh.
- Add deterministic paper-artifact validation, durable research-lit pool/manifest/selection/finalization/Step 8 orchestration, and same-topic score-preservation checks.
- Align cross-skill contracts for verified-only composed analysis, `论文范围`, unresolved figures, quote placeholders, and non-destructive wiki deduplication.
- Return concise arXiv CLI errors instead of uncaught RuntimeError tracebacks.
- Refactor research-lit into a compact agent-facing controller plus one-level execution contracts, add source/no-evidence receipts and merge-preserving candidate rules, and move Semantic Scholar reference fetching behind verification and cheap binary topical admission.
- Add deterministic verified-only binary-relevance filtering that preserves full candidate metadata before reference fetching.
- Exclude candidates without a supported PDF route from deep-analysis selection, persist exclusion reasons, and refresh selected PDF paths before dispatch.
- Align landscape synthesis with the complete-artifacts-only overview policy and clarify that Step 8 relationship writes are separate from page ingestion.
- Add auditable prior same-topic reference-cache import, bounded Semantic Scholar CLI retry/timeout controls, and computation-only Step 5 scoring.
- Reorder Step 6 reference materialization before analysis dispatch and extend the final audit to source, overview-section, and Step 8 receipts.
