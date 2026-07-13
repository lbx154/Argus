---
name: Story State Update
description: Extract what the chapter CHANGED into a structured state_patch.json and apply it through the safe patch engine to produce story_state.json. Never hand-rewrite the whole state. Enforces idempotency, valid id references, no silent deletion, parseable timeline. The state_update stage of fiction_writing.
category: fiction-state
version: 1
protected: true
---

## Title
Story State Update

## Description
Keep long-term continuity by turning each chapter into a small, validated DELTA
to `story_state` — not a rewrite. The writer emits a `state_patch.json`; the
engine (`argus_skill.verticals.fiction_writing.state.apply_patch`) validates and
applies it with hard safety guarantees so prior setup is never silently lost.

## Category
fiction-state

## When to use
- The `state_update` stage of `fiction_writing`, right after a draft.
- Any time the canonical `story_state` must reflect new/changed characters,
  relationships, world rules, locations, items, timeline entries, threads,
  foreshadowing, or chapter summaries.

Do NOT use to write prose or to hand-edit `story_state.json` directly.

## How to solve
1. **Diff the draft against the current state.** List exactly what the chapter
   introduced or changed — and nothing it did not. Every new entity gets an
   `id`; reuse existing ids for existing entities.
2. **Emit `fiction/state_patch.json`** with a unique `patch_id` and an `ops`
   array using ONLY these ops (there is deliberately no delete op):
   `set_meta`, `add_character`, `update_character`, `add_relationship`,
   `add_world_rule`, `add_location`, `add_item`, `move_item`, `add_timeline`,
   `add_open_thread`, `resolve_thread`, `add_foreshadowing`,
   `resolve_foreshadowing`, `add_chapter_summary`.
   - Required `value` shapes (the engine rejects a missing `name`/`id`):
     `add_character {id,name,...}`, `add_location {id,name}`,
     `add_item {id,name,holder?,location?}`, `add_timeline {id,order:int,label}`,
     `add_open_thread {id,statement}`, `add_foreshadowing {id,statement}`;
     `update_character {id, set:{...}}`, `move_item {id, to_holder|to_location}`,
     `add_chapter_summary {chapter:int, summary}`.
   - `holder` MUST be an existing CHARACTER id; a thing resting at a PLACE uses
     `location` (a location id), never `holder`. Add the location/character
     before referencing it (the engine rejects dangling ids).
   - death/exit = `update_character set.status=dead|absent` (never remove them);
   - a character learning something = `update_character set.knows=[…]`;
   - an item changing hands = `move_item to_holder/to_location` (never teleport);
   - a revealed setup = `resolve_foreshadowing`; a closed question = `resolve_thread`.
3. **Apply through the engine**, not by hand:
   ```python
   from argus_skill.verticals.fiction_writing.state import apply_patch
   new_state, result = apply_patch(old_state_or_None, patch)
   ```
   The engine guarantees: idempotent by `patch_id`; atomic (a bad op rejects the
   whole patch, nothing partial); no silent deletion; all id references must
   resolve; unique-integer timeline order. Persist `new_state` to
   `fiction/story_state.json`; `result.revision` bumps and `applied_patches`
   records the `patch_id`.
4. **On a PatchError**, fix the patch (usually a dangling id, a duplicate add, or
   a timeline order clash) — do NOT bypass the engine.

## When NOT to use
- To draft or revise prose.
- To force a change the engine refuses — the refusal is protecting continuity.

## Common pitfalls
- Rewriting the whole state JSON (drops history; forbidden).
- Erasing a dead character instead of marking status.
- Reusing a `patch_id` across different changes (idempotency will drop it).
