---
name: paper-analysis
description: Use this skill to analyze one research-paper PDF. Full mode produces note.md, note.json, logic.json, evidence.json, and extracted images. Explicit note-only mode produces exactly one note.md for survey workflows.
argument-hint: [pdf-path] [paper-folder-or-note-path] [mode] [verification-status] [authors] [published-date] [discovery-source]
allowed-tools: Bash(*), Read, Write, Glob, Grep
---

# Paper Analysis: Structured Notes with Insights

## Inputs

- **MODE** — `full` by default, or explicit `note-only`. Never infer note-only mode from the output path.
- **NOTE_PATH** — required only in `note-only` mode. Absolute path of the single Markdown file to write.

- **PDF_PATH** — path to the paper's PDF, already downloaded (or present) under the paper's folder.
- **PAPER_FOLDER** — required in `full` mode: the destination folder for the PDF and structured artifacts.
- **VERIFICATION_STATUS** — one of `verified` / `unverified` / `verify_pending` / `error`, plus `verification_method` (`arxiv` / `crossref` / `s2` / `none`). A research-lit dispatch always supplies `verified`; standalone callers may deliberately supply another status, which is preserved as a caveat.
- **AUTHORS** — the paper's first 3 authors, supplied by the caller.
- **PUBLISHED_DATE** — the best available ISO date (`YYYY-MM-DD`), supplied by the caller.
- **DISCOVERY_SOURCE** — the source that first surfaced the paper, supplied by the caller.
- **TOOLS_DIR** — `/Users/ninnnnk/Projects/my-auto-research-in-sleep/tools`. First-party scripts this skill (and research-lit generally) depends on live here as flat files, including `figure_extractor.py`. This skill itself lives at `/Users/ninnnnk/Projects/my-auto-research-in-sleep/skills/paper-analysis`.
- **FIGURE_EXTRACTOR_REPO** — `/Users/ninnnnk/Projects/figure-extractor`. The actual cloned Huang-lab/figure-extractor repo (with its `core/`, `app/`, `pdffigures2/` etc.), kept intact and separate from TOOLS_DIR. `TOOLS_DIR/figure_extractor.py` has `sys.path.insert(0, "/Users/ninnnnk/Projects/figure-extractor")` added at the top (before its `from core...` imports) so it can resolve `core` from this location despite living outside the repo.

## Note-Only Mode Contract

When and only when `MODE=note-only`:

1. Read `PDF_PATH`, judge the paper type as in Step 0, and write the Step 1 analytical note directly to `NOTE_PATH`.
2. Follow the complete Step 1 section order, depth, quotation, formula, language, and heading requirements below. Metadata supplied by the caller may be used to disambiguate identity, but does not replace reading the PDF.
3. Write exactly one file: `NOTE_PATH`. Do not create `PAPER_FOLDER`, `note.json`, `logic.json`, `evidence.json`, `images/`, `references.json`, temporary extraction artifacts, or any other output.
4. Do not run the figure extractor. Keep every figure/table placeholder as text. If a corresponding image cannot be extracted, the placeholder remains unchanged; it is never converted to a broken image link.
5. Validate that `NOTE_PATH` exists and contains the paper title plus `Abstract`, `Challenges`, `Methodology`, `Results`, `Limitations`, and `Insights` headings. Then stop. Steps 2–4 do not apply.

The research-lit controller owns batch task preparation and final note validation. A note-only subagent must not modify candidate metadata, ranking, synthesis, or query-pack files.

## Step 0: Judge the Paper's Type

Before writing the note, judge whether the paper is:
- **Method-centric**: the paper proposes one core method/architecture, and its results exist mainly to validate that single proposal (plus ablations on it).
- **Finding-centric / multi-experiment**: the paper's results are organized around several largely independent empirical claims, each backed by its own distinct experiment (as in an empirical study, measurement paper, or analysis paper) — a paper with many parallel Setup+Results units, or a paper whose own structure highlights several separate takeaways, is a sign of this type.

This judgment determines how Results is structured below. Full mode also records it as `paper_type` in `note.json` during Step 2; note-only mode does not create that file.

## Step 1: Write note.md

Draft the note itself using the paper-notes-prompt spec below in full — this is the authoritative content and format spec for note.md. Write all figure/table references as text placeholders per the spec's PLACEHOLDERS representation — do not attempt to resolve them to real image files yet; that happens in Step 3, after the full analysis exists.

<content_requirements>
## Sections for Notes
The notes MUST be organized into the following sections, in this order: Abstract, Challenges, Methodology, Results, Limitations, Insights. Each section should include well-structured information. Only Challenges and Methodology carry bolded **Key Insight** callouts; Abstract, Results, Limitations, and Insights do not require one. A section is not limited to exactly one Key Insight, draft as many as the paper's own text genuinely supports, each in the same fixed format; do not force multiple distinct insights into one to hit a count of one, and do not manufacture extra insights just to pad the count.

1. **Abstract**
   - Provide a summary of the main topic or problem addressed by the paper.
   - Describe the proposed method or solution briefly.
   - Highlight the key findings or contributions of the paper.
   - Explain the significance of the paper in its field.
   - Do not include figure/table placeholders in Abstract. Image placeholders must attach to an ID-bearing claim or result so they can be represented faithfully in `evidence.json`.

2. **Challenges**
   - Scope of this section is strictly about **how the problem was discovered and defined**.
   - Clearly describe the initial problem the paper aims to solve.
   - Detail the logical progression or derivation of deeper or equivalent problems from the initial problem.
   - Explain why the authors identified these problems and how they reached these conclusions through analysis, experiments, observations, or theoretical reasoning.
   - **Positioning against prior work**: identify, as concretely as the paper allows, which specific existing methods, assumptions, or lines of work this paper is built on, arguing against, or filling a gap relative to — based on what the paper itself states in its Introduction/Related Work. Name the approach/limitation being targeted whenever the paper names it.
   - **Key Insight(s)**: Highlight and mark in **bold** each deep insight related to *how the problem/challenge itself was identified or framed* — one or more, per the note above. Each gets a direct quote of the original text (see Citation rules); the bolded claim text itself must restate that quote's point in your own words, not echo its phrasing.
   - If a figure/table directly supports a Key Insight, place its standalone placeholder immediately after that Key Insight's supporting material. Do not put a placeholder in an un-IDed summary or positioning paragraph.

3. **Methodology**
   - Provide a DETAILED explanation of the method or solution proposed by the authors.
   - Break down the core components of the method and explain their roles.
   - Explain the overall workflow, including input, processing pipeline, and output when applicable.
   - Discuss why the method is effective.
   - If a figure/table directly supports a Key Insight, place its standalone placeholder immediately after that Key Insight's supporting material. Do not put a placeholder in an un-IDed summary paragraph.
   - **Key Insight(s)**: Highlight and mark in **bold** each deep insight related to the methodology's design choices — one or more, per the note above. Each gets a direct quote of the original text (see Citation rules); the bolded claim text itself must restate that quote's point in your own words, not echo its phrasing.

4. **Results**
   - Summarize all the evaluation metrics and benchmarks used in the paper.
   - Regardless of paper type, write Results as a sequence of numbered items. Every item follows this fixed 5-part internal structure, in this order:
     1. A short numbered title distilling the point/claim, styled as a label rather than a full sentence, in Title Case (capitalize the first letter of every major word, not sentence case) — e.g. "1. Intrinsic Rewards Inevitably Collapse Despite Tuning" — numbered in order across the items in this section. Per Note Presentation Format below, this title is rendered as its own heading, one level deeper than the Results section heading.
     2. A direct quote of 1–3 consecutive original sentences from the PDF supporting that point (per Citation Rules). This is **mandatory, not conditional** — every item gets one. If the specific claim in the title doesn't have an obviously quotable sentence sitting right next to it, search more broadly in that experiment's own subsection for the sentence stating the result itself. Find the full sentence containing that phrase instead. If, after searching, no matching sentence can be confidently located, use a placeholder for the quote (see Citation Rules) rather than forcing a mismatched quote or dropping the item.
     3. The experimental setup that tested it (dataset, model, comparison baseline), together with a PLACEHOLDER for the figure/table corresponding to this specific item. This placeholder is **mandatory whenever the paper itself names a specific Figure/Table for this item's own result** — if the quote in step 2, or the surrounding text you drafted this item from, explicitly references "Table N" / "Figure N", that is not incidental phrasing to skip past — insert the matching `*[Placeholder for Table N: ...]*` / `*[Placeholder for Figure N: ...]*` for it. Silently omitting the placeholder because the item is a secondary/ablation result rather than the Main Result is not acceptable — Step 3 can only resolve a placeholder that Step 1 actually wrote.
     4. The result: describe the quantitative or qualitative outcome in detail, following the authors' original description of the result. Include the reported values, comparisons, trends, conditions, and observed effects that the paper uses to characterize the finding. Do not reduce the result to a brief metric or a single number.
     5. The authors' own interpretation or understanding of this specific item — why they think the result occurred, what mechanism they attribute it to, or what they say it implies. Restate that interpretation accurately in your own words; the item's required blockquote is the sole verbatim evidence. This stays local to what the authors say about *this one item*, not the note-taker's broader synthesis across the paper.
   - **If method-centric** (per Step 0): item 1 is the Main Result — the paper's primary evaluation (core benchmark/dataset, metric, comparison against baselines or prior state of the art). Every subsequent item is a Secondary Experiment, in the order the paper itself presents them. Secondary experiments often include ablation studies, but are not limited to that — they may also include robustness or generalization tests, efficiency/scaling analysis, qualitative case studies, human evaluation, or transfer to other tasks/domains, depending on whatever the paper actually runs. Cover all distinct secondary experiments.
   - **If finding-centric / multi-experiment** (per Step 0): items are the paper's distinct findings/topics, in the order the paper itself organizes them — its own section/subsection breakdown, explicitly labeled takeaways, or clearly separate named experiments. Do not collapse multiple distinct findings into one generic item, and do not select only the single most prominent finding — cover all of them.
   - The number of items should track what the paper itself organizes around (1 main result + N secondary experiments, or N findings) — not the note-taker's own judgment of what's "most important." A paper with many parallel experiments producing many items is expected and correct — do not compress it to look concise at the cost of dropping items.

5. **Limitations**
   - Cover what the authors themselves acknowledge as limitations, scope constraints, or open weaknesses. Each distinct limitation becomes one `L1`, `L2`, ... item in `logic.json`/`evidence.json`, in the same order as its prose paragraph in note.md.
   - Quote per the Citation rules below where the wording matters. Start directly with the content itself.

6. **Insights**
   - The essence of the problem as the authors themselves understood it.
   - Why the proposed method works and what the paper attributes that effectiveness to in detail.
   - The implications and future directions the paper itself draws.

## Formula Extraction by Section
All formula-extraction requirements are consolidated here rather than repeated per section. When drafting each section, consult this list to determine whether — and how — that section should surface formulas in the $$ environment.

- **Abstract**: No formula extraction expected. Formulas are not part of this section's scope even if the paper's abstract mentions one in passing.
- **Challenges**: Conditional. If the paper formally defines the problem or its objective with an equation (e.g., a loss function, an optimization objective, a formal problem statement), extract it in the $$ environment rather than describing it only in prose. If no such formal definition exists in the paper, omit — do not force one.
- **Methodology**: Mandatory, not optional. Every formula/equation central to the method's core mechanism must be reproduced in the $$ environment exactly as it appears in the paper; a prose description alone is not a substitute. This is the section where the bulk of formula extraction is expected to occur.
- **Results**: Conditional. If a key evaluation metric is defined by a formula in the paper (rather than being a standard, universally known metric), extract it in the $$ environment instead of only naming the metric. Standard, widely known metrics (e.g., accuracy, BLEU) do not need their formula reproduced.
- **Limitations**: No formula extraction expected. This section is prose-driven and quote-driven, not formula-driven.
- **Insights**: No formula extraction expected. This section synthesizes implications rather than restating technical definitions.

## Depth of Content
- Prioritize being **concise, comprehensive, and accurate** — cover everything that matters, but do not pad with restatement or filler.
- Prioritize **depth over concision** — but depth means engaging with more of the paper's implications.
- Throughout, describe the reasoning process, not just conclusions — how the authors arrived at an understanding, not only what they concluded.
- There is no fixed word count, but each paragraph should be the length a human researcher would naturally write for that point.

## Citation Rules
- Direct quotes are copied **verbatim, 1–3 full sentences, from the original PDF text** — never paraphrased inside the blockquote, and never more than three sentences per quote.
- A quote must be at least one complete sentence, not a short phrase or keyword fragment pulled out of a sentence.
- No separate paraphrase sentence sits next to the quote itself. Instead, restating the quote's point in your own words happens inside whichever field you're already required to write for that same claim — the bolded Key Insight `text`, or a Results item's `author_interpretation` — never by echoing the quote's own phrasing back in that field.
- Wherever a quote is called for (Key Insight in Challenges/Methodology, each Results item, Limitations where the wording matters), it is not optional/skippable when a marked schema field allows `null` for it elsewhere in this document — `null` there means "this specific paper genuinely has no such claim to report," not "a quote wasn't found." Search the PDF text itself for the sentence stating the specific number, comparison, or claim before concluding none exists.
- **If a claim is real (the paper clearly makes it) but, after searching, no matching sentence can be confidently located to quote verbatim**: do not force a mismatched quote, and do not silently drop the insight/item to avoid the problem. Use a placeholder instead — `*[Placeholder for quote: <what this claim needs a verbatim source sentence for>]*` — the same philosophy Step 3 already applies to unresolved figure/table placeholders, just for text.

## Writing Style Rules
- Write the way a knowledgeable human researcher would write their own academic notes: natural, fluent, formal register — not stiff or robotic, and not colloquial either. This governs every section.
- Keep paragraphs at a natural human length.
- **No meta-commentary about your own explanation or the paper's argument, anywhere in the note.** State the substance directly rather than describing that you're about to state it or how it's organized.

</content_requirements>

<format_requirements>
## Notes Format Requirements
- **Headings**:
  * ALL headings, at every heading level used in the note, MUST be in English. Only the body content under headings is written in Chinese.

- **Note Title**:
  * Use the TITLE OF THE PAPER as the title of the note.

- **Title Hierarchy**:
  * Use the second-tier title `##` for the title of the note.
  * Use the third-tier title `###` for each chapter (Abstract, Challenges, Methodology, Results, Limitations, Insights).
  * Inside Results only: each numbered item's title (content_requirements, Results, part 1 of the 5-part structure) is its own fourth-tier heading `####`, e.g. `#### 1. Main Result: State-of-the-Art BLEU on WMT En-De/En-Fr` — this is the one deliberate exception to "no further heading level," chosen specifically so each item's title renders bold and visually larger than body text, matching how a human researcher's own notes would visually break up a long Results section.
  * Do not add a further heading level inside Insights, or inside any other section besides the Results exception above.

## Notes Presentation Format
- **Structure**:
  * Follow the section order defined in Sections for Notes above.

- **Citation**:
  * Direct quotes (Challenges/Methodology/Limitations/Results) use blockquote formatting — 1–3 sentences copied verbatim from the original PDF:
    > Example of a direct quote from the paper.
    > A second or third sentence may follow if needed.

- **Text Format**:
  * Default to flowing paragraphs, not bullet lists (see Writing Style Rules) — this is how Limitations and Insights in particular should read. Results is an explicit exception for both paper types: it is written as a sequence of numbered items per the 5-part template in content_requirements (title → quote → setup+placeholder → result → interpretation) — one item for the Main Result plus one per Secondary Experiment for method-centric papers, or one per finding for finding-centric papers. Each item's title is a `####` heading per the Title Hierarchy exception above; do not introduce any other heading level beyond what Title Hierarchy specifies.

## Formula Representation
- Represent any mathematical equations or formulas using the $$ environment, ensuring clear and accurate formatting.
- Which sections require or permit formula extraction is governed by "Formula Extraction by Section" above — this subsection only governs the visual/syntactic representation once a formula is included.
- In `logic.json`, store the same formula as raw LaTex without surrounding `$$` delimiters. The renderer that writes note.md adds those delimiters; JSON never does.
- Example:
  $$E = mc^2$$

## PLACEHOLDERS representation
- While drafting (Step 1): *[Placeholder for Figure 2: Illustration comparing self-supervised and supervised contrastive losses]*
- After Step 3 resolves it to a real file: `![Figure 2: Illustration comparing self-supervised and supervised contrastive losses](images/<basename from renderURL>)` — reference figure-extractor's output filename in place, never copy or rename it. Plain markdown image syntax only — do not wrap it in a `<div align="center">` or any other raw-HTML wrapper: tried once, and it broke image display in Obsidian, because Obsidian's relative-path resolution for `![...]()` (rewriting `images/...png` into an actual vault resource URL) applies to top-level Markdown image syntax and doesn't reliably apply to the same syntax nested inside a raw HTML block, even with blank-line separation. A working left-aligned image beats a broken centered one; if centering is wanted later, it needs an Obsidian-native mechanism (a CSS snippet/plugin), not an in-note HTML wrapper.
- **Always its own standalone paragraph, in both forms above**: a blank line immediately before it and a blank line immediately after it, on its own line, never embedded mid-sentence or mid-paragraph — even when the surrounding prose says "(see Figure 7)" or "as shown in Figure 7 below" inline. The sentence referencing the figure stays inline in the prose as normal text; the placeholder/image itself is a separate block that follows (or precedes) that paragraph. 

</format_requirements>

## Step 2: Write note.json, logic.json, and evidence.json

This is the same extraction as note.md, structured for machine/downstream use, split across three files by role rather than one flat file — paper-level bookkeeping, the paper's own claims/reasoning, and the raw verbatim material backing those claims are each a different consumer's concern, and physically separating them lets a downstream skill load only what it needs. Every claim/result gets a stable `id` — `KI-1`, `KI-2`, `KI-3`, ... sequentially across *all* key insights from both Challenges and Methodology combined, in the order drafted, then `R1`, `R2`, ... for Results items, then `L1`, `L2`, ... for distinct Limitations paragraphs. The *same* `id` appears in both `logic.json` and `evidence.json`, and that shared `id` is the only binding mechanism between them; no separate pointer field is needed. Draft all three directly alongside note.md. Every evidence entry starts with `images: []` because real images do not yet exist; Step 3 appends resolved or unresolved image records. Any quote that cannot be confidently matched in the PDF text is the placeholder text described in Citation Rules, not a fabricated string.

**`note.json`** — paper-level metadata only, nothing from the analytical sections:
```json
{
  "paper_id": "<arxiv id, DOI, or a stable slug if neither exists>",
  "title": "<paper title>",
  "authors": ["<first author>", "<second author>", "<third author>"],
  "published_date": "<YYYY-MM-DD, from AUTHORS/PUBLISHED_DATE inputs, passed through unchanged>",
  "discovery_source": "<zotero | obsidian | semantic_scholar | arxiv, from DISCOVERY_SOURCE input>",
  "verification_status": "<verified | unverified | verify_pending | error, from Step 4, passed through unchanged>",
  "verification_method": "<arxiv | crossref | s2 | none>",
  "paper_type": "<method-centric | finding-centric, from Step 0>"
}
```
Do not invent venue or score fields that were not supplied by the caller.

**`logic.json`** — the paper's own claims and reasoning, no verbatim quotes or image paths. `key_insights` is always an array — empty if the section genuinely has none, one entry if there's exactly one, more if the paper's own text supports several distinct insights. Each `text` (Key Insight) and `author_interpretation` (Results) must restate its paired `evidence.json` quote in your own words — never echo the quote's phrasing back here:
```json
{
  "paper_id": "<same as note.json>",
  "abstract": "<prose, same content as the Abstract section of note.md>",
  "challenges": {
    "summary": "<prose>",
    "positioning_against_prior_work": "<prose>",
    "formulas": [{"latex": "<raw LaTex, no $$ delimiters>", "note": "<optional explanation>"}],
    "key_insights": [{"id": "KI-1", "text": "<the bolded claim, in your own words>"}]
  },
  "methodology": {
    "summary": "<prose>",
    "formulas": [{"latex": "<raw LaTex, no $$ delimiters>", "note": "<the design-choice note that followed it, if any>"}],
    "key_insights": [{"id": "KI-2", "text": "<the bolded claim, in your own words>"}]
  },
  "results": {
    "items": [
      {
        "id": "R1",
        "role": "<main_result | secondary_experiment (method-centric) | finding (finding-centric)>",
        "title": "<the numbered short title, in Title Case, without the leading number>",
        "setup": "<prose>",
        "result": "<prose>",
        "author_interpretation": "<prose, accurately paraphrasing the authors' explanation>",
        "formulas": [{"latex": "<raw LaTex, no $$ delimiters>", "note": "<optional metric explanation>"}]
      }
    ]
  },
  "limitations": {"items": [{"id": "L1", "text": "<prose, in your own words>"}]},
  "insights": "<prose>"
}
```

**`evidence.json`** — the raw, verbatim material proving each `logic.json` claim, keyed by the same `id`s. Every entry uses the same shape; `images` is an empty array when the claim has no figure/table placeholder. A `quote` value is either a real verbatim quote or, when Citation Rules' placeholder policy applies, the placeholder text itself — never a fabricated approximation:
```json
{
  "paper_id": "<same as note.json>",
  "entries": {
    "KI-1": {"quote": "<verbatim 1-3 full sentences, or a placeholder>", "images": []},
    "R1": {"quote": "<verbatim 1-3 full sentences, or a placeholder>", "images": [{"source_figure": "Figure N", "image_ref": "images/<basename from renderURL>", "caption": "<same caption used in note.md>", "status": "resolved"}]},
    "L1": {"quote": "<verbatim limitation sentence(s), or a placeholder>", "images": []}
  }
}
```

- `logic.json`'s `results.items` order/count and `evidence.json`'s `R*` entries must match note.md exactly, and `entries` must contain exactly one key per `logic.json` id (every `key_insights[].id`, every `R*`, every `limitations.items[].id`) — this is the same fan-out reasoning re-expressed as data, not a separate summarization pass. The count of key insights and limitations is not fixed.
- If a field has no content for this paper (for example a section has no formulas or a claim has no image placeholders), use an empty array rather than omitting the key, so downstream consumers can rely on a stable shape.

## Step 3: Extract Real Images and Replace Placeholders

Only after note.md, note.json, logic.json, and evidence.json all exist (Steps 1–2) does this step run — it patches note.md and evidence.json in place, it does not redo the analysis. `logic.json` is never touched by this step — it has no image references to resolve.

`*[Placeholder for Figure N: ...]*` in note.md records an intended figure/table binding. It becomes a real image reference only when the extractor produces the exact corresponding asset; otherwise it remains visible text, rather than being replaced with a misleading approximation. Region-accurate cropping is done via **pdffigures2**, wrapped by **Huang-lab/figure-extractor** for a plain CLI interface.

**Per-paper extraction:**
```bash
mkdir -p "$PAPER_FOLDER/images"

python3 "$TOOLS_DIR/figure_extractor.py" "$PDF_PATH" --local --output-dir "$PAPER_FOLDER/images"
# Produces cropped images plus a JSON metadata file with each figure's name (e.g. "3" for
# "Figure 3"), figType, page, bounding box, caption text, and renderURL.
```

Match each `*[Placeholder for Figure N: ...]*` / `*[Placeholder for Table N: ...]*` to a metadata entry whose `figType` and `name` exactly equal the requested type and number, then confirm its `renderURL` basename exists under `images/`. Read that basename from metadata rather than hardcoding a filename or suffix. Do not copy, rename, or substitute an image from a different region/page.

For every placeholder, once resolved to a real file under `images/`, patch both files:
- In note.md, replace the placeholder text with a real markdown image reference (`![Figure N](images/<basename from renderURL>)`) followed by `*Figure N. <caption>*`, both as standalone paragraphs with blank lines before and after. Never substitute inline or wrap the image in raw HTML. If a pre-existing draft put the placeholder mid-sentence, pull the resolved image block beside that sentence instead of literally replacing it inline.
- In evidence.json, append `{"source_figure": "Figure N", "image_ref": "images/<basename from renderURL>", "caption": "<same caption used in note.md>", "status": "resolved"}` to the matching entry's `images` array. Look up the entry by its `id` (`KI-*` or `R*`).

If the exact figure/table metadata entry or its declared image file is missing, do not rasterize the page and do not replace the placeholder. Leave its text in note.md and append `{"source_figure": "Figure N", "image_ref": null, "caption": "<placeholder description>", "status": "unresolved"}` to the matching evidence entry's `images` array. Do not delete the reference or fabricate a path.

**Before finishing this step, run an explicit generate → validate → fix loop** with `python3 "$TOOLS_DIR/validate_paper_artifacts.py" "$PAPER_FOLDER"`. It checks quote-to-PDF exactness (allowing only extraction layout normalization), schema/ID coverage, formulas, headings, result count, and resolved/unresolved image bindings. Fix every mismatch and rerun until it exits 0. If no reliable source quote can be matched, use exactly `[Quote unavailable in extracted text; manual PDF verification required.]` instead of an approximation.

## Step 4: Metadata and Verification Status Are Carried Through, Not Recomputed

Neither this step recomputes anything — every value here was already known before this skill was dispatched (Inputs above), so it's placed, not derived.

**Order under the note title, both as single metadata lines, in this order:**
1. `*Authors: <AUTHORS, comma-separated> · Published: <PUBLISHED_DATE> · Source: <DISCOVERY_SOURCE>*`
2. `*Verification: <status icon + label>*` (e.g. `*Verification: ⚠️ unverified (verification unavailable)*`)

Both lines are metadata about the extraction, not a Results/Insights claim, so they sit outside the six content sections and aren't subject to Writing Style Rules' no-meta-commentary rule (that rule governs commentary *within* the analytical sections, not these two factual lines).

Also set in note.json's top-level fields (Step 2), verbatim: `authors`, `published_date`, `discovery_source`, `verification_status`, `verification_method`. `logic.json` and `evidence.json` carry no metadata fields beyond their own `paper_id`.

This skill never re-runs or second-guesses the supplied verdict. In research-lit composed use, only verified papers are dispatched. In standalone use, an explicitly supplied `unverified` or `verify_pending` paper may still be extracted, with the status line carrying the caveat.

## Output

On completion in full mode, `PAPER_FOLDER/` contains: the PDF (if downloaded), `note.md`, `note.json` (metadata), `logic.json` (claims/reasoning), `evidence.json` (quotes/image bindings), and `images/`. A caller that needs `references.json` owns that separate artifact.
