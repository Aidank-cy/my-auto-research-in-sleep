# Paper Extraction

Read this file in Stage 3.2 before extracting one paper into:

```text
synthesis/extraction/<paper-id>.md
```

Use `synthesis/packets/index.json` to locate and read that paper's source packet.

Use the field schemas in `node-schema.md`. This stage discovers source-local
Evidence plus paper-local and domain-level Logic candidates. It does not decide
canonical retention.

## Output

```markdown
# <Paper title>

- **Paper ID**: <paper-id>
- **Source kind**: pdf | local-note | metadata
- **Evidence level**: fulltext | local-note | metadata-only
- **Read scope**: <full page range, note path, or metadata>
- **Packet SHA256**: <packet text_sha256>
- **Extraction status**: complete

## Evidence

## Claims

## Problems

## Heuristics
```

Use globally unique block IDs by including the paper ID, for example
`^evidence-p31-multiturn-drop` and `^claim-p31-multiturn-reliability`. Logic
entries link to Evidence in the same file as `[[#^evidence-...]]`. Heuristics
link to Problems in the same file as `[[#^problem-...]]`. Set `Scope` on every
extracted Logic entry using `node-schema.md`.

## Source Coverage

Downloaded and local PDFs arrive as page-marked source packets. Follow the
section-focused reading protocol in Stage 3.2. For full text, read Abstract,
Introduction, method overview, relevant Results/Evaluation,
Discussion/Limitations, and Conclusion by function rather than literal section
name. A combined section may satisfy multiple targets; use page-marked packet
text when a target section is absent. Use these regions as navigation cues for
the default and targeted reads:

- Claim: Abstract, Introduction, Results, Conclusion.
- Problem: Introduction, Related Work, Limitations, Conclusion.
- Heuristic: Method, Design, Ablation, Discussion.
- Evidence: Results, Experiments, Ablation, Proof, Case Study.

Section names are navigation cues, not classification rules. Record only what
the packet actually states. Apply the same discovery gates and status
definitions to `fulltext`, `local-note`, and `metadata-only` packets. Evidence
level records the available source form and does not independently determine a
Logic node's status.

A `metadata-only` packet may produce Evidence, Claims, Problems, and Heuristics
when their required fields are explicitly present in the title, abstract, TLDR,
or other included metadata. Set its Evidence type to `metadata`. A `local-note`
packet may produce the same node kinds under the same field and grounding rules
as a full-text packet. Preserve `metadata-only` or `local-note` in the extraction
header and preserve the available packet or note locators in Provenance.

## Evidence-First Procedure

1. Locate source passages relevant to the survey question.
2. Create Evidence for each atomic reported fact, result, observation,
   theoretical result, or explicit source statement.
3. For each Evidence, ask:
   - What verifiable statement does it support or refute?
   - Does it establish a Current state, a source-stated Desired state, and the
     Gap between them?
   - Does it support a reusable action for closing a Problem?
4. Create the corresponding Claim, Problem, or Heuristic candidates.
5. Run the completion checks below.

A passage may produce Evidence plus several Logic candidates. The four kinds
are not mutually exclusive labels for source passages. When an author states a
Claim, Problem, or Heuristic without direct validation, first record the
statement as `source-statement` Evidence and preserve that boundary.

## Discovery Gates

### Evidence

- Preserve the source report without agent interpretation or cross-paper
  synthesis.
- Store one independently meaningful finding per entry.
- Include `paper:<paper-id>` plus section, page, packet, and note locators that
  are actually available in `Provenance`.
- Keep agent-derived conclusions for domain synthesis.

### Claim

- The Statement must be supportable, refutable, or verifiable and link to at
  least one Evidence entry.
- Normalize wording without broadening the source's object, conditions,
  direction, strength, or scope.
- Use `hypothesis` when the source states the proposition without empirical or
  theoretical validation.
- Treat a reported measurement as Evidence. Add a Claim only when the source
  interprets it as a more general verifiable proposition.

### Problem

- Create a Problem only when Current state, Desired state, and Gap are all
  source-grounded.
- Link Current state to Evidence. Take Desired state from an explicit objective,
  requirement, or ideal state in the source.
- Do not turn a limitation, failure, or low score into a complete Problem when
  the source provides no Desired state.
- Leave Explanation empty when the source gives no causal account.

### Heuristic

- Write an actionable, reusable Prescription that targets at least one Problem.
- Take Rationale from the source and leave it empty when absent.
- A description such as "the paper uses X" is not a Heuristic. Extract one only
  when the source supports using action X under condition Y to reach goal Z or
  reduce a Gap.
- Exclude implementation details that only serve the paper's code path and do
  not transfer to another research object, method, or evaluation setting.

## Completion Checks

- Every Claim, Problem, and Heuristic links to at least one Evidence entry.
- Every Heuristic links to at least one Problem.
- Every Logic node uses `Scope: paper-local` or `Scope: domain-level` according
  to its descriptive fields.
- Every node expresses one independently meaningful unit.
- Every source statement resolves to a paper and an available section, page,
  packet, or note locator.
- Missing Explanation, Rationale, Desired state, or other source content stays
  empty; do not complete it from general knowledge.
- Merge semantic duplicates within the paper while preserving every supporting
  Evidence link and locator.
- Allow zero to many entries of each kind. Do not fill quotas.
- Set `Extraction status` to `complete` only after all four node sections and all
  source-grounding checks have been reviewed.

## Example

For a reported average 39% drop from single-turn to multi-turn evaluation
across 15 models and six tasks:

- Evidence records the scoped measurement.
- A Claim may state the source's supported proposition that multi-turn
  interaction reduces reliability under the evaluated conditions.
- A Problem exists only if the source also specifies the desired predictive
  evaluation state and the missing interaction coverage.
- A Heuristic exists only if the source justifies a reusable controlled
  single-turn/multi-turn comparison for exposing that gap.
