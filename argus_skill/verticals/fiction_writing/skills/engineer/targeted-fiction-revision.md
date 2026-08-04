---
name: "Targeted Fiction Revision"
description: "Apply the reviewer's findings to produce fiction/final.md and fiction/updated_story_state.json. Fix every BLOCKING continuity contradiction with a concrete change; treat craft/AI-flavor notes as non-blocking (fix or accept with rationale). Do targeted edits, not a full rewrite. The revise stage of fiction_writing."
---

## Title
Targeted Fiction Revision

## Description
Close the loop: take `review.json` and the draft, resolve the blocking issues
with minimal, targeted edits, and reconcile the state. Produce the final prose
and an updated, consistent `story_state`.

## Category
fiction-revision

## When to use
- The `revise` stage of `fiction_writing`, after a review.
- To address specific, located findings — not to re-imagine the chapter.

Do NOT use to draft from scratch or to re-plan the arc.

## How to solve
1. **Read `review.json`** and sort findings by severity. Every `blocking`
   continuity finding MUST be fixed; `major`/`minor`/`note` craft items are
   fixed when cheap, otherwise accepted with a one-line rationale (craft is
   judgment, not a gate).
2. **Edit surgically.** For each blocking finding, change the specific span the
   review cited; avoid collateral rewrites that could introduce NEW
   contradictions. Preserve the parts the review did not flag.
3. **Reconcile state.** If a fix changes a fact (a character's status/knowledge,
   an item's holder, a resolved thread), emit the corresponding `state_patch`
   ops and re-apply through `apply_patch` (never hand-edit). Write
   `fiction/updated_story_state.json`.
4. **Write `fiction/final.md`** and verify it is consistent with
   `updated_story_state.json` and that no blocking finding remains. If a finding
   cannot be fixed without a re-plan, say so explicitly rather than papering over
   it.

## When NOT to use
- When there is no review yet.
- To silently overhaul the chapter (targeted edits only).

## Common pitfalls
- "Fixing" prose while leaving the state inconsistent with the final text.
- Introducing a new contradiction while resolving another.
- Treating a blocking continuity break as an optional style note.
