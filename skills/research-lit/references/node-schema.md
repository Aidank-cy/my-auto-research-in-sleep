# Research Node Schema

Read this schema before writing scoped extraction nodes in Stage 3.2 or
canonical Logic in Stage 3.3. Evidence records source-faithful findings; Logic
records Claims, Problems, and Heuristics grounded in Evidence. The extraction
compiler projects valid scoped Evidence into canonical Evidence. Keep every
node atomic.

## Output Contexts

Scoped nodes live together in `synthesis/extraction/<paper-id>.md` under
`Evidence`, `Claims`, `Problems`, and `Heuristics`. Include the paper ID in each
scoped block ID, for example `^evidence-p31-multiturn-drop` and
`^claim-p31-multiturn-reliability`. Link within that file with
`[[#^evidence-...]]` and `[[#^problem-...]]`.

The compiler-produced canonical Evidence and Agent-written canonical Logic use
these layouts at `synthesis/evidence/evidence.md` and
`synthesis/logic/logic.md`:

```markdown
# Literature Evidence

## Evidence
```

```markdown
# Literature Logic

## Claims

## Problems

## Heuristics
```

Canonical Logic links to Evidence with
`[[../evidence/evidence#^evidence-<slug>]]`; canonical Heuristics link to
Problems in the same file with `[[#^problem-<slug>]]`.

## Shared Formatting

Use a display number, concise title, and stable Obsidian block ID:

```markdown
### E1: <title> ^evidence-<slug>
### C1: <title> ^claim-<slug>
### P1: <title> ^problem-<slug>
### H1: <title> ^heuristic-<slug>
```

- Number each kind independently in document order.
- Write `<slug>` in lowercase ASCII kebab-case and keep block IDs unique and
  stable.
- Preserve the field labels and order shown in each template.
- Write `Statement`, `Current state`, `Desired state`, `Gap`, and `Prescription`
  as one atomic sentence each.
- Write `Explanation` and `Rationale` as one to three source-grounded sentences;
  leave the value empty when the source provides no explanation.
- Write `Evidence` as one or more comma-separated links to the applicable
  Evidence blocks.
- Write `Targets problem` as one or more comma-separated links to the
  applicable Problem blocks.
- Write `Tags` as one to five space-separated Obsidian tags in lowercase
  kebab-case.
- Write Logic `Provenance` as `<origin>: <sources>` using `source`, `survey`,
  `user`, or `mixed`. Use paper-note links when they exist and `paper:<paper-id>`
  otherwise.

## Evidence

Evidence is one independently meaningful, source-faithful finding extracted
from a paper. Logic entries link to Evidence through their `Evidence` fields.

```markdown
### E1: Multi-turn failures in agent evaluation ^evidence-multiturn

- **Finding**: The paper reports that multi-turn conversation settings reveal reliability failures not captured by static or single-turn evaluation settings.
- **Context**: model: LLM agents; task: multi-turn conversation; setting: static versus multi-turn evaluation
- **Evidence type**: empirical-result
- **Limitations**: The reported effect is evaluated on the paper's selected tasks and interaction settings.
- **Tags**: #agent-evaluation #multi-turn #reliability
- **Provenance**: paper:p31; section: Results; pages: 4-5; packet: p31-results; note: [[LLMs Get Lost In Multi-Turn Conversation]]
```

Write `Finding` as one atomic sentence that preserves what the source reports.
Write `Context` as semicolon-separated `<dimension>: <value>` pairs using only
dimensions supported by the source, such as `model`, `task`, `dataset`, and
`setting`.

Use exactly one `Evidence type` value:

```text
empirical-result | theoretical-result | source-statement |
secondary-report | metadata
```

- `empirical-result`: a result from an experiment, benchmark, ablation,
  dataset, case study, or observed failure.
- `theoretical-result`: a theorem, proof, derivation, or formal result.
- `source-statement`: a method, design, definition, rationale, or assertion
  stated by the source without direct empirical or theoretical validation.
- `secondary-report`: the paper reports a result attributed to another source.
- `metadata`: support is limited to title, abstract, or bibliographic metadata.

Write `Limitations` only from boundaries stated by the paper or directly
implied by the reported context. Leave it empty when none are available. Start
`Provenance` with `paper:<paper-id>`, then add available `section`, `pages`,
`packet`, and `note` segments separated by semicolons. Omit unavailable
segments; never invent a locator.

## Logic Scope

Use exactly one `Scope` value for every Claim, Problem, and Heuristic:

```text
paper-local | domain-level
```

Scope describes the breadth of the object, process, phenomenon, constraint, or
prescription expressed by the Logic node. It is independent of how many papers
support the node. Evidence nodes do not use Scope.

- `paper-local`: the node depends on a paper-specific method, system,
  implementation, experimental configuration, or result. Removing that
  identity changes or destroys the node's meaning.
- `domain-level`: the node describes a reusable class of objects, processes,
  phenomena, constraints, or design principles within the survey domain. It
  remains meaningful and verifiable without the originating paper's identity.

Determine Scope from `Statement`; `Current state`, `Desired state`, and `Gap`;
or `Prescription` and `Targets problem`. One paper may produce both scopes. A
domain-level node may have Evidence from one paper, and multiple papers may
support a paper-local node without changing its Scope. Preserve the scope
supported by the source; never enlarge a paper-specific result solely because
it appears important or repeats elsewhere.

## Claim

A Claim is a verifiable statement about a system, process, method, dataset,
task, relation, limitation, or field.

```markdown
### C1: Static benchmarks overestimate agent reliability ^claim-static-benchmarks

- **Statement**: Static single-turn benchmarks tend to overestimate agent reliability in realistic multi-turn interaction.
- **Status**: supported
- **Explanation**: Static tasks omit state drift, clarification, recovery, and evolving user preferences that introduce additional failure modes.

- **Evidence**: [[../evidence/evidence#^evidence-multiturn]], [[../evidence/evidence#^evidence-preference]]
- **Scope**: domain-level
- **Tags**: #agent-evaluation #multi-turn
- **Provenance**: survey: paper:p31, paper:p67
```

Use exactly one `Status` value:

```text
hypothesis | supported | refuted
```

- `hypothesis`: current evidence is insufficient or mixed.
- `supported`: linked evidence supports the statement within scope.
- `refuted`: linked evidence directly contradicts the statement within scope.

## Problem

A Problem is a structured gap between an evidenced current state and a desired
observation, explanation, verification, or performance state.

```markdown
### P1: Interactive reliability gap ^problem-interactive-reliability

- **Current state**: Many agent evaluations use static, single-turn, or short-horizon tasks.
- **Desired state**: Evaluations reliably predict performance in long-horizon, multi-turn, tool-using interaction.
- **Gap**: Current evaluations often omit failures caused by state drift, clarification, recovery, and evolving user preferences.
- **Status**: open
- **Explanation**: Static task construction removes interaction dynamics that create these failures.
- **Importance**: high: Resolving the gap would change how the field estimates practical agent reliability.

- **Evidence**: [[../evidence/evidence#^evidence-multiturn]]
- **Scope**: domain-level
- **Tags**: #agent-evaluation #reliability
- **Provenance**: survey: paper:p31, paper:p67
```

Use exactly one `Status` value:

```text
open | partially-addressed | contested | resolved
```

- `open`: no adequate solution reaches the desired state.
- `partially-addressed`: existing work closes part of the gap.
- `contested`: linked evidence disputes the gap's existence, framing, or extent.
- `resolved`: the desired state is achieved within scope.

Write `Importance` as `<level>: <community impact>` using `low`, `medium`, or
`high`:

- `low`: impact is confined to one paper, method, or narrow setting.
- `medium`: impact reaches a subfield or several related approaches.
- `high`: impact changes domain-level conclusions, evaluation, explanation, or
  broadly used research practice.

Base importance on the stated community impact and linked evidence. Treat
`Scope` and `Importance` as independent fields.

## Heuristic

A Heuristic is an actionable prescription for moving from a current state
toward a desired state. It includes methods, mechanisms, protocols, design
rules, implementation tricks, data rules, and reusable negative lessons.

```markdown
### H1: Use multi-turn tool traces for reliability evaluation ^heuristic-multiturn-traces

- **Prescription**: Evaluate agents on multi-turn tasks with tool traces, recovery opportunities, and evolving user state.
- **Rationale**: These settings expose failures hidden by static or single-turn benchmarks.
- **Targets problem**: [[#^problem-interactive-reliability]]
- **Status**: supported

- **Evidence**: [[../evidence/evidence#^evidence-multiturn]]
- **Scope**: domain-level
- **Tags**: #evaluation-protocol #tool-use
- **Provenance**: survey: paper:p31, paper:p67
```

Use exactly one `Status` value:

```text
proposed | supported | contested | invalidated
```

- `proposed`: feasibility or benefit is not yet established.
- `supported`: linked evidence shows feasibility or effectiveness within scope.
- `contested`: linked evidence disagrees about effectiveness or conditions.
- `invalidated`: linked evidence shows failure within scope.
