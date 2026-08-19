# Candidate Workflow Contract

## Artifact Ownership

`research_candidate_search.py` is the only normal search entrypoint. It owns source execution, merge, candidate IDs, relevance text, internal identity checks, candidate admission, and initial receipts.

- `source_status.json`: source attempts and warnings plus separate current-run (`usable_candidate_count` / `current_run_usable_candidate_count`) and historical merged (`historical_candidate_count`) counts.
- `verification_status.json`: detailed verifier audit. It may contain `confidence`, `reason`, and discovered identifiers.
- `candidate_metadata.json`: complete merged audit pool. Candidate objects expose historical `sources`, current-run `current_run_sources`, `verification_status`, and `verification_method`; detailed confidence and reasons remain in `verification_status.json`.
- `candidate_papers.json`: admitted minimal records used for model relevance screening.
- `search_summary.json`: source and candidate-admission gates.

Hugging Face `summary`, `ai_summary`, and TLDR fields may support candidate screening and `relevanceText`, but they are not authoritative abstracts, experimental results, Claim evidence, or Relation evidence.

## Identity Resolution And Admission

Merge by normalized arXiv ID, DOI, Semantic Scholar paper ID, then normalized title. Preserve source membership, nested source payloads, alternate values, and merged IDs. Prefer arXiv metadata for title, authors, abstract, and arXiv URLs; prefer Semantic Scholar for venue, DOI, publication type/date, and citation count when present.

Identity checks run in this order: arXiv ID, Crossref DOI, Semantic Scholar fuzzy title. Stable `verified` and `unverified` outcomes may be cached; transient `verify_pending` must not be cached. HTTP 429 and 5xx remain transient failures; they do not trigger an alternate source that silently admits the paper.

The unified search command owns one `--sleep` interval for Hugging Face, arXiv, Semantic Scholar, and identity-check HTTP attempts. It enforces that minimum across source boundaries and retries; stricter source-specific pacing and server-provided cooldowns still apply.

The script writes only status `verified` to `candidate_papers.json`. Other outcomes remain in metadata for audit, while downstream stages consume the admitted file and do not repeat this decision.

## Source Failure Handling

When one source fails, inspect `source_status.json`, `search_summary.json`, and stderr in that order. Read the failing source implementation only when those receipts cannot explain the error; do not invoke a wrapped source fetcher to reconstruct the candidate pool outside the unified command.

For each Semantic Scholar search query, allow up to 15 retries with a two-second
base interval and honor a longer server-provided `Retry-After`. Route citation
title and identifier lookups through the same policy. Exhausted HTTP 429 and
5xx responses remain source failures without fallback; preserve partial results
and leave unavailable citation counts missing.

## Rerun Rules

Re-run the same unified command. Reuse stable verifier cache records and source caches where the tools support them. Preserve prior `pN` IDs for matched papers and append newly discovered papers after the existing ID range; deterministic dedupe retains unavoidable aliases in `merged_ids`. Existing valid PDFs, packets, and note files are reused downstream.

## Admission And Ranking

The Agent labels only records from `candidate_papers.json`, using title and `relevanceText`/abstract against the confirmed topic and constraints. Apply this compact rubric:

- `1`: the paper's central problem, method, or main evaluation directly addresses the topic's object and target variable or outcome.
- `0.5`: the paper provides substantive partial evidence, a relevant mechanism, or useful adjacent context, but the topic is not its central question.
- `0`: the match is incidental, concerns a different object or outcome, or the available text is too weak to establish substantive relevance.

Write one top-level numeric value with no rationale and keep all records. Do not use citations, venue, upvotes, popularity, or source count to assign relevance.

Citation enrichment is post-relevance and optional per candidate. Missing citation data is zero only for ranking mechanics, not a factual claim that the paper has zero citations.

Ranking eligibility is the conjunction:

```text
verification_status == verified AND relevance > 0
```

Eligible records rank by relevance (`1` before `0.5`), citation threshold/count, Hugging Face upvote threshold/count, then original order. The top configured count is selected; only selected verified papers with successful arXiv PDFs receive note-only deep-analysis tasks. Ineligible records remain in `candidate_ranking.json` with an exclusion reason.
