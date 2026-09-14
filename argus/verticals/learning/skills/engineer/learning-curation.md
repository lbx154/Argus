---
name: "Learning Curation Execution"
description: "Turn operator material into evidence-anchored project Skill/Wiki edits or an honest no-op."
---

Read the immutable material pages and search the existing project Skill/Wiki
libraries before editing. Record the decision in `learning/CHANGE_PLAN.json`
with `version: 1` and an `operations` list.

For `create`, `update`, or `archive`, name a project-relative `target`, its
`layer` (`skill` or `wiki`), a reason, and at least one evidence span containing
`source_id`, `locator`, and a verbatim `quote` from the staged material. Create
or update targets before curation completion. Never target global libraries,
escape the project root, or manufacture generated identity suffixes.

When nothing durable should change, emit one `no_op` operation with the honest
reason. Do not combine `no_op` with writes. Keep `learning/STUDY.md` concise and
update the project Wiki `INDEX.md` when Wiki content changes.