# Selective Local Sync Report — V5

**Date:** 2026-07-14
**Mode:** selective cherry-pick (NO `git pull`, NO full rebase, NO merge, NO force reset)
**Verdict:** ✅ 38 non-physics commits synced; protected physics pipeline byte-identical; tests green.

## 1. Identity

| item | value |
|---|---|
| branch | `physics-thin-mainline-sync` |
| pre-sync HEAD | `7214363d` |
| origin/main (at sync) | `cb1e44ed` |
| backup branch | `backup/physics-v5presync-7214363d` |
| post-sync HEAD | `68413d65` (+38 picks +1 manifest regen) |

`git cherry HEAD origin/main`: **58 genuinely-new** commits (`+`), **24 already applied** (`-`,
from the prior V5-precursor sync, detected by patch-equivalence so nothing double-applied).

## 2. Disposition of the 58 new commits

- **A — skipped (our base physics):** `8ad4d92a feat(physics): add paper-style manuscript delivery`.
- **B — cherry-picked (`-x`): 38.** All non-physics, none touch protected files. Highlights:
  - `81535928 feat(research): make final paper-writing idea-centric with honest reframing`
    — **directly relevant to the over-defensive-disclaimer problem (issue 六)**; modifies the
    NLP/academic vertical (not physics), so it is a **reference pattern** to mirror into the
    physics manuscript contract in Phase A4 (idea_centrality dimension, central-thesis+insight
    check, scope-to-regime / boundary-analysis directives, integrity floor).
  - `c0b0abc3 / 59a9c9ae / 7c4b90f7 / 885c8467` planner/backlog dynamic-plan features.
  - `6941ec54 / d2c45d21` research/reviewer web-evidence validation.
  - docs, webapi bounds, tui recovery, slash-command surface, perf(stream persistence).
- **C — deferred (touch protected backend files; not whole-file overwritten):**
  - `297ca301` (loop.py), `80641e48` (manager/_core.py), `bb4f6f1d` (manager/_core.py + vertical_select.py), `7bd43af0` (loop.py).
- **Source-conflict — deferred (aborted, not force-resolved):**
  - `008512cd` (reviewer/_parsing.py) — reviewer progress-class fix.
  - **`06691811` (engineer/runner.py + life/supervisor), `a5f9cd95` + `1a9b2cf6`
    (life/supervisor/_planning_cycle.py)** — the **planner "reject unversioned plan revisions" /
    "bypass stale idle gates on replan"** chain. **These are the most relevant to BUG 1**
    (planner-verdict idle stall) but form a dependent chain rooted in the deferred `06691811`,
    so they conflict as a set. **Flagged for manual integration** (see BUG_REPORT_FOR_DEBUG.md /
    TIMEOUT_LIMIT_AUDIT.md) — worth pulling in deliberately to test whether they fix the manuscript
    repair-loop idle.
  - frontend build-artifact refreshes (`ff0a3eec`, `a1ddee02`, `cf0c904a`, `a7e017e0`, `fa9ef86c`,
    `cf438361`, `923bc5cb`, `93c1582a`, `ff920ace`, `a7f29aae`, `cb1e44ed`) — `frontend/*/dist` +
    `tui/bundle/argus.mjs` collisions; regenerable by the frontend build, no backend impact.

## 3. Generated files

Only `4aab5a64` produced a generated-file conflict (resolved by taking incoming, then regenerating).
After all picks, `scripts/generate_release_manifest.py` regenerated `release_manifest.json` +
`release.generated.ts` and they were committed. Digest reflects the synced frontend TS sources; all
`argus_skill/**/*.py` backend unchanged.

## 4. Protected pipeline integrity

Byte-identical to `backup/physics-v5presync-7214363d`: `skills/capability_registry.py` (V3 adapter),
`skills/research_gates.py`, `skills/manuscript_repair.py`, `verticals/physics/stages.py`,
`verticals/physics/manuscript.py`, `verticals/physics/gates/*`, `manager/_core.py`, `loop.py`,
`tools/stage_check.py`, `skills/vertical_select.py`. → **V3 adapter / research gates / manuscript
repair loop fully intact.**

## 5. Tests

219 passed (registry + V3 adapter, research_gates, 5 physics gates, manuscript repair,
manuscript/paper-style contracts, stage_decider, stage_check acceptance, release). 0 failed.

## 6. Constraints

No push, no merge, no force reset. zimo2 / external missions untouched. Backup branch retained.
