# Domain Logic Synthesis

Read this file in Stage 3.3 after the extraction compiler reports `valid`.
Use `node-schema.md` for canonical Logic fields and Evidence-link forms.

## Inputs And Outputs

Inputs:

```text
synthesis/extraction/candidate_index.jsonl
synthesis/extraction/<paper-id>.md
synthesis/evidence/evidence.md
synthesis/packets/index.json
synthesis/packets/<paper-id>.json
```

Model-written outputs:

```text
synthesis/extraction/clusters.md
synthesis/logic/logic.md
```

Extraction retains every source-grounded Logic candidate. Canonical Logic
contains normalized domain-level nodes. Cross-paper comparison deduplicates,
aggregates Evidence, and exposes conflicts; paper count does not determine
Scope.

## Pass 1: Compare Candidates

Compare candidates separately by kind. Use tags only to shortlist comparisons.
Decide identity from these descriptive fields:

- Claim: `Statement`.
- Problem: `Current state`, `Desired state`, and `Gap`.
- Heuristic: `Prescription` and `Targets problem`.

Match the object, direction, conditions, and scope expressed in the fields.
Shared labels, goals, datasets, or keywords do not establish identity. Keep
broader, narrower, conditional, opposing, and materially different
prescriptions separate.

Apply these retention rules:

- A relevant domain-level candidate may map directly to canonical Logic,
  including when it has Evidence from one paper.
- Merge equivalent or compatible domain-level candidates under the strongest
  common supported formulation.
- Keep paper-local candidates in extraction regardless of how many papers
  discuss the same paper-specific object.
- Paper-local candidates may support a domain-level synthesis only when their
  combined Evidence supports an explicit common object and scope. Repetition
  alone does not justify generalization.
- Every evidence level is eligible. Preserve Evidence links and Provenance so
  source limitations remain visible downstream.

## Pass 2: Write The Decision Ledger

Use this exact shape:

```markdown
# Canonical Decisions

## Claims

### CCL1: <decision title> ^cluster-claim-<slug>

- **Members**: [[p31#^claim-p31-...]]
- **Match fields**: Statement
- **Canonical node**: [[../logic/logic#^claim-...]]
- **Reason**: <why this scope and mapping are justified>

## Problems

### PCL1: <decision title> ^cluster-problem-<slug>

- **Members**: ...
- **Match fields**: Current state; Desired state; Gap
- **Canonical node**: [[../logic/logic#^problem-...]]
- **Reason**: ...

## Heuristics

### HCL1: <decision title> ^cluster-heuristic-<slug>

- **Members**: ...
- **Match fields**: Prescription; Targets problem
- **Canonical node**: [[../logic/logic#^heuristic-...]]
- **Reason**: ...

## Excluded Candidates

- [[p12#^claim-p12-...]]: paper-local scope tied to Method X.
```

Every candidate in `candidate_index.jsonl` appears exactly once, either in one
Members field or in the exclusion ledger. A decision contains one or more
candidates and points to exactly one canonical node of the same kind. A
single-member decision is valid only for a domain-level candidate.

## Pass 3: Write Canonical Domain Logic

- Write one canonical node for each retained decision and no additional nodes.
- Set `Scope` to `domain-level`.
- Keep wording within the scope supported by every member and its Evidence.
- Set `Evidence` to the exact union of all member Evidence links.
- Include every member paper ID in `Provenance` with origin `source`, `survey`,
  or `mixed`.
- Keep explanations and rationales source-grounded. Leave them empty when no
  shared explanation is available.
- Use conservative statuses when support differs across members.
- A canonical Heuristic targets canonical Problems present in `logic.md`.

Relations between distinct canonical nodes belong to `relations.md`.

## Completion Checks

- Every Logic candidate is mapped once or explicitly excluded.
- Every decision and canonical node map one-to-one.
- Every single-member decision starts from a domain-level candidate.
- Every canonical node uses `Scope: domain-level`.
- Every Evidence and Problem link resolves.
- Canonical wording stays within the supported object, conditions, and scope.
