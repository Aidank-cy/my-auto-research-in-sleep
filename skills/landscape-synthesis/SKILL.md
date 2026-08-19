---
name: landscape-synthesis
description: Use this skill to synthesize a set of already-analyzed papers into a single field-level overview.md — consensus/disagreements, mainstream approach clusters and their lineage, a challenge-resolution matrix, direct contradictions, evaluation landscape, shared limitations, cluster momentum, gaps, and the paper reference table. Called once per topic from research-lit's Step 7, not once per paper.
argument-hint: [topic] [obsidian_topic_folder] [paper_folder_list]
allowed-tools: Bash(*), Read, Write, Glob, Grep
---

# Landscape Synthesis: Field-Level Literature Analysis

## Inputs

- **TOPIC** — the research topic string this landscape is about.
- **OBSIDIAN_TOPIC_FOLDER** — where `overview.md` is written, at this folder's own root (sibling to the per-paper subfolders, not nested inside any one of them).
- **PAPER_FOLDERS** — the full list of per-paper folders from research-lit's Step 6. Each folder contains: `note.json` (paper metadata — `title`, `published_date`, `venue`, `discovery_source`, and Step 6-patched `relevance_score` with `total`/`internal_citation_graph`/`hf_freshness`/`topical_relevance`/`novelty`/`cited_by_within_set`); `logic.json` (the paper's `challenges`, `methodology`, `results.items`, `limitations.items`, and `insights`, with stable IDs `KI-*` for key insights, `R*` for results, and `L*` for limitations); and `evidence.json` (verbatim quotes and resolved/unresolved image bindings keyed by those IDs; see Citation Rules). Use this list for both the narrative synthesis and the 论文范围 table; research-lit passes it without a prebuilt table.
- **COMPOSED** (optional) — if the caller passed `— composed: <path>`, do not write `overview.md` as a standalone file; return the same content for the orchestrator to fold into its own report instead. The paper table still folds into that returned content in the same position described below.

## Constants

- **MAX_TABLE_ROWS = 20** — cap on the 论文范围 table, sorted by Score descending. Omit excess papers from the table only; cover every paper in `PAPER_FOLDERS` in the narrative.

## Content Requirements

### Sections for overview.md

Write the sections in this order, using and cross-referencing the supplied fields.

1. **领域问题概览与核心挑战**
   - Identify the major problems or challenges from `logic.json`'s `challenges` across *all* papers in `PAPER_FOLDERS`. 
   - Synthesize closely related challenges into one paragraph per problem, explaining what each problem is, how it manifests, and why it exists. Account for all papers' challenges without paper-by-paper repetition.
  
2. **主流方法路线**
   - Group papers by the problem or phenomenon their methodology addresses, using `logic.json`'s `methodology` and `challenges`; merge closely related problems even when terminology differs.
   - Assign each paper to the primary problem it addresses. A paper may also appear in another cluster when it makes a substantial contribution to that problem, but avoid repetitive descriptions.
   - Within each problem-oriented cluster, identify the main methodological approaches and explain how they differ in their assumptions, mechanisms, or intervention points.
   - Describe each cluster's methodological lineage — what later work builds on, extends, combines, or departs from — only when supported by `methodology`, `insights`, or explicit comparisons.

3. **跨方法比较**
   - Commonalities and differences between the clusters from section 2.
   - A challenge-resolution matrix: challenges (section 1) × clusters (section 2), each cell rated on three tiers:
     - *已充分解释或解决* — methods, experiments, and results provide a sufficiently complete explanation or solution to the problem, without substantial unresolved conflict across the relevant papers.
     - *已有解释但存在冲突* — the problem has received substantive explanations or proposed solutions, but relevant papers provide conflicting conclusions, mechanisms, interpretations, or resolutions.
     - *尚未充分解释或解决* — existing work provides only partial evidence, preliminary exploration, or an incomplete explanation or solution.

4. **共识、分歧与直接矛盾**
   - Synthesize consensus and disagreements among papers addressing the same problem or phenomenon.
   - Treat claims as a **direct contradiction** only when they address the same problem or phenomenon and make mutually incompatible claims that cannot both hold under the stated interpretation.
   - Provide a **separate, explicit list** of direct contradictions. For each contradiction, identify the shared problem or phenomenon, state the opposing claims, and cite both papers by title or `paper_id` and by claim ID from `logic.json` — using `insights` or `results.items[].author_interpretation` (for example, “R2 in paper X contradicts KI-2 in paper Y”).

5. **评测 Benchmark**
   - Extract benchmarks and datasets from `logic.json`'s `results.items` across all papers, and identify which dominate based on usage frequency.
   - Summarize any remarks made by the papers about a benchmark or dataset being saturated, unreliable, limited, or otherwise unsuitable for evaluating progress.

6. **研究趋势与共同局限**
   - For each major problem or phenomenon identified in Sections 1 and 2, analyze how research attention has changed over time using the `published_date` of the relevant papers.
   - Within each problem-oriented cluster, compare the different methodological approaches and identify which approaches appear to be rising, stable, or fading, using publication recency and `novelty` from `relevance_score` as supporting signals.
   - Do not classify an approach as rising or fading from `novelty` alone; interpret publication recency and novelty together.
   - Group `logic.json`'s `limitations.items` by related methods, explanations, or solution strategies. Summarize repeatedly reported limitations, name the papers that acknowledge them, treat independent reports by multiple related papers as stronger evidence than one-off reports, and keep unrelated approaches separate.


7. **缺口与机会**
   - Synthesize Sections 1–6 into concrete research gaps and opportunities for the user's own work.
   - Derive each gap from evidence established earlier, such as a problem that remains insufficiently explained or solved in Section 3, a disagreement or direct contradiction in Section 4, an unreliable or saturated benchmark in Section 5, or a shared limitation of a method or explanatory framework in Section 6.
   - For each gap, explain what is currently missing, why the gap matters, and what kind of research direction, method, experiment, or evaluation could address it.
   - Prioritize gaps that are supported by multiple papers, affect a major problem or widely used method, or could resolve an important contradiction in the literature.

### 论文范围

Place this table after Section 7 (`缺口与机会`).

```
| 论文 | 发表场合 | 分数 | 方法 | 关键结果 | 与本主题的相关性 | 来源 |
| ----- | ----- | ----- | ------ | ---------- | --------------- | ------ |
```

Column sources, one row per paper folder in `PAPER_FOLDERS`:
- **论文** ← `note.json` `title`.
- **发表场合** ← `note.json` top-level `venue`.
- **分数** ← `note.json` `relevance_score.total`.
- **方法** ← `logic.json` `methodology.summary`.
- **关键结果** ← `logic.json` `results.items` (Main Result for method-centric; representative finding(s) for finding-centric).
- **与本主题的相关性** ← judged here, against `logic.json`'s `challenges`/`insights` vs `TOPIC`.
- **来源** ← `note.json` top-level `discovery_source`. Papers in `PAPER_FOLDERS` already passed research-lit's Step 4 verification gate, so this column needs no verification badge.

**Row cap**: sort by Score descending, keep top `MAX_TABLE_ROWS` (see Constants); close the table with a one-line count of remaining papers (e.g. "+47 more").

Records without completed deep-analysis artifacts never reach `PAPER_FOLDERS` and must not appear in the overview. Their status remains auditable in research-lit's run artifacts.

If Obsidian notes exist (surfaced upstream by research-lit's Step 1), incorporate the user's own insights into whichever section above they're most relevant to — most often 4 or 7.

### Depth of Content
- Be **concise, comprehensive, and accurate**; scale the length with `len(PAPER_FOLDERS)` and do not pad sections when the evidence is thin.
- Sections 4 and 7 carry the primary actionable value of the file — prioritize depth over concision there specifically.
- Throughout, name papers by title (or `paper_id` if the title is long) whenever a specific claim, contradiction, or limitation is attributed to a specific paper.

### Citation Rules
- Quote only **1–2 verbatim sentences from `evidence.json`**, using the same claim/result ID assigned in `logic.json`; do not create quotes during synthesis.
- Most claims here should be attribution (naming which paper says what) rather than quotation.

### Writing Style Rules
- Use a natural, fluent, formal research-survey register throughout.
- Keep paragraphs at a natural length; split overloaded paragraphs and avoid sentence fragments.
- **No meta-commentary about your own explanation or organization, anywhere in the file.** Do not narrate what you're about to cover, just state the substance. 
- Do not treat the section list above as a checklist to be answered one bullet at a time. Each section should read as continuous prose that naturally covers what's asked, not a set of separately-flagged mini-answers.
- Keep proper nouns — paper titles, dataset/benchmark names, model/method names, venues, tool names — in their original form as used in the papers, not translated, even where the surrounding prose is in Chinese. Translate all general descriptive language, table/section labels, challenge ratings, and narrative synthesis into Chinese.

## Format Requirements

- **Headings**: ALL headings and table labels in `overview.md` are in Chinese. Proper nouns inside headings may remain in their original English form only when translating them would reduce recognizability.
- **Title hierarchy**: `##` for the file title (Chinese translation of the topic), `###` for each of the 7 Chinese sections above, then `### 论文范围` after Section 7 for the embedded paper table. The challenge-resolution matrix (section 3) and the contradictions list (section 4) may use a table or list respectively within their section — this does not count as an additional heading level.
- **Text format**: use flowing paragraphs, the section-3 challenge-resolution matrix, and the final 论文范围 table. Use a list only for section 4's contradiction entries; use prose elsewhere.

## Output

Write `OBSIDIAN_TOPIC_FOLDER/overview.md` (or return the equivalent content to the caller, per the `COMPOSED` input above).
