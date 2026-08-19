# Note-Only Analysis Handoff

`research_note_tasks.py prepare` is the deterministic bridge from PDF downloads to paper-analysis subagents. It selects only successful arXiv PDF download/reuse records whose candidate is verified, then writes `synthesis/paper_note_tasks.json`.

Each task contains the exact PDF path, note path, identity, first three authors, publication date, venue, verification fields, `mode: note-only`, and an input signature. Dispatch one subagent per pending task and pass these values without reinterpretation. Prepare marks a note pending when it is missing, structurally invalid, or stale against its PDF/identity input; otherwise it is reusable.

Dispatch only tasks marked `pending`. After they finish, run central validation; retry only tasks that remain invalid.

In note-only mode, paper-analysis writes exactly the assigned Markdown file. It must not create JSON, images, references, a paper folder, or temporary extraction output. Keep unresolved figure and table placeholders as text rather than Markdown images because figure extraction is outside this mode.

After dispatch, validation requires the exact title, six ordered substantive sections, numbered Results items, and a quote or approved quote placeholder per Results item. A pending pre-existing note must also differ from its preparation-time hash, preventing an unchanged invalid or stale note from being promoted. Validation records hashes in `synthesis/validation/paper_notes.json`, promotes valid tasks to `reusable`, keeps invalid tasks `pending`, and writes only valid routes to required `synthesis/wiki_notes.json`. Packet and query-pack builders independently enforce this gate.
