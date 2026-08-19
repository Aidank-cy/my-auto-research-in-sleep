# Relation Data

Read this file in Stage 3.4 after canonical compilation reports `valid`.

## Procedure

1. Propose relations from canonical Domain Logic, the decision ledger, and
   scoped extraction files.
2. Reopen every listed full-text, local-note, or metadata packet.
3. Keep a relation only when its direction and type are supported by those
   packets.
4. Write the verified relation to `synthesis/logic/relations.md`.

An empty Relations section is valid. Citation count, venue, title similarity,
source overlap, and shared tags are not relation evidence.

## Markdown Format

```markdown
# Literature Relations

## Relations

### R1: Preference evaluation extends interaction reliability ^relation-preference-extends-reliability

- **From**: paper:p67
- **Type**: extends
- **To**: paper:p31
- **Evidence**: p67 extends the interaction setting in p31 by testing whether agents discover and satisfy evolving user preferences.
- **Confidence**: medium
- **Source packets**: p67, p31
- **Source nodes**: paper:p67, paper:p31, [[../evidence/evidence#^evidence-p67-preference]], [[../evidence/evidence#^evidence-p31-multiturn]]
- **Evidence level**: fulltext
- **Read scope**: p67:pages:1-18; p31:pages:1-12
```

Use one-line fields in the displayed order. `From` and `To` each contain one
paper node or one Obsidian block link. `Source packets` is a comma-separated
list of packet IDs. `Source nodes` contains the supporting paper and C/P/H/E
nodes.

## Relation Types

```text
extends | contradicts | addresses_gap | inspired_by |
tested_by | supports | invalidates | supersedes
```

Source and target conventions:

- `extends`: `paper:* -> paper:*` or `claim:* -> claim:*`.
- `contradicts`: `paper:* -> paper:*`, `evidence:* -> evidence:*`, or
  `claim:* -> claim:*`.
- `addresses_gap`: `paper:* | claim:* | heuristic:* -> problem:*`.
- `inspired_by`: `claim:* | problem:* | heuristic:* -> paper:*`.
- `supersedes`: `paper:* -> paper:*`.
- `tested_by`: `claim:* | idea:* -> exp:*`.
- `supports` and `invalidates`: `exp:* -> claim:* | idea:*`.

Research-lit normally emits `extends`, `contradicts`, `addresses_gap`,
`inspired_by`, and `supersedes`. The remaining types apply when downstream idea
or experiment nodes already exist.

## Required Values

- `Confidence`: `low | medium | high`.
- `Evidence level`: `metadata-only | local-note | fulltext | mixed`.
- `Read scope`: one compact locator per source packet.

Metadata-only support uses `low` confidence. `mixed` applies when source packets
have different evidence levels. The compiler projects this Markdown to
`relations.jsonl` and validates endpoints, packets, source nodes, levels, and
confidence. Until idea/experiment schemas are integrated, `idea:*` and `exp:*`
IDs pass existence checks as externally owned; type, direction, and provenance
checks still apply.
