# JSONL Projections

This file documents the deterministic projections produced by
`tools/research_synthesis_compile.py` from:

```text
synthesis/logic/logic.md
synthesis/evidence/evidence.md
```

Markdown is canonical. JSONL files are machine projections for validators,
promotion planning, and query packs.

## Mapping

- Emit one JSON object per line and project every canonical field.
- Remove the leading `^` and replace the first hyphen with a colon when mapping
  block IDs to node IDs: `^claim-static-benchmarks` becomes
  `claim:static-benchmarks`.
- Convert Markdown field names to lowercase snake_case.
- Convert Obsidian evidence and problem links to node IDs.
- Strip the leading `#` from projected tags.
- Record the canonical block in `source_block`.

## Claim

Claim entries map to `synthesis/logic/claims.jsonl`:

```json
{"node_id":"claim:static-benchmarks","kind":"claim","title":"Static benchmarks overestimate agent reliability","statement":"Static single-turn benchmarks tend to overestimate agent reliability in realistic multi-turn interaction.","status":"supported","explanation":"Static tasks omit interaction dynamics that introduce additional failure modes.","evidence_nodes":["evidence:multiturn","evidence:preference"],"scope":"domain-level","tags":["agent-evaluation","multi-turn"],"provenance":"survey: paper:p31, paper:p67","source_block":"synthesis/logic/logic.md#^claim-static-benchmarks"}
```

## Problem

Problem entries map to `synthesis/logic/problems.jsonl`:

```json
{"node_id":"problem:interactive-reliability","kind":"problem","title":"Interactive reliability gap","current_state":"Many agent evaluations use static, single-turn, or short-horizon tasks.","desired_state":"Evaluations reliably predict performance in long-horizon, multi-turn, tool-using interaction.","gap":"Current evaluations often omit failures caused by interaction dynamics.","status":"open","explanation":"Static task construction removes interaction dynamics that create these failures.","importance":"high: Resolving the gap would change how the field estimates practical agent reliability.","evidence_nodes":["evidence:multiturn"],"scope":"domain-level","tags":["agent-evaluation","reliability"],"provenance":"survey: paper:p31, paper:p67","source_block":"synthesis/logic/logic.md#^problem-interactive-reliability"}
```

## Heuristic

Heuristic entries map to `synthesis/logic/heuristics.jsonl`:

```json
{"node_id":"heuristic:multiturn-traces","kind":"heuristic","title":"Use multi-turn tool traces for reliability evaluation","prescription":"Evaluate agents on multi-turn tasks with tool traces, recovery opportunities, and evolving user state.","rationale":"These settings expose failures hidden by static or single-turn benchmarks.","target_problem_nodes":["problem:interactive-reliability"],"status":"supported","evidence_nodes":["evidence:multiturn"],"scope":"domain-level","tags":["evaluation-protocol","tool-use"],"provenance":"survey: paper:p31, paper:p67","source_block":"synthesis/logic/logic.md#^heuristic-multiturn-traces"}
```

## Evidence

Evidence entries map to `synthesis/evidence/evidence.jsonl`:

```json
{"node_id":"evidence:multiturn","kind":"evidence","title":"Multi-turn failures in agent evaluation","finding":"The paper reports that multi-turn conversation settings reveal reliability failures not captured by static or single-turn evaluation settings.","context":"model: LLM agents; task: multi-turn conversation; setting: static versus multi-turn evaluation","evidence_type":"empirical-result","limitations":"The reported effect is evaluated on the paper's selected tasks and interaction settings.","tags":["agent-evaluation","multi-turn","reliability"],"provenance":"paper:p31; section: Results; pages: 4-5; packet: p31-results; note: [[LLMs Get Lost In Multi-Turn Conversation]]","source_block":"synthesis/evidence/evidence.md#^evidence-multiturn"}
```

The compiler validates Markdown before replacing any projection. Projection
hashes are recorded in `synthesis/manifest.json` and checked before downstream
builders run.
