# Selective Local Sync Report

**Date:** 2026-07-14
**Mode:** selective cherry-pick (NO full rebase, NO `git pull`, NO `git merge`, NO force reset)
**Verdict:** ✅ Non-physics `origin/main` updates selectively synced. Local research
capability gates / manuscript repair loop / V3 adapter kept intact. Deferred all
mixed and source-conflicting commits (nothing force-resolved). Ready for the fresh
Haldane mission.

## 1. Branch / HEAD / backup

| item | value |
|---|---|
| current branch | `physics-thin-mainline-sync` |
| original HEAD (pre-sync) | `70888eaa` |
| origin/main HEAD (at sync) | `d93d1b08` |
| **backup branch** | `backup/physics-thin-mainline-sync-70888eaa` (= `70888eaa`) |
| current HEAD (post-sync) | `9d16c108` |

`HEAD..origin/main` had **36** commits. Working tree was clean before and after.

## 2. Classification & disposition

- **A — skipped (our previously-merged base physics):**
  - `8ad4d92a feat(physics): add paper-style manuscript delivery` — the paper-style /
    manuscript-delivery base we already carry (our branch has the superset:
    manuscript repair loop + research gates on top). **Not taken.**
- **B — cherry-picked (`-x`), non-physics, touch NO protected files:** 23 commits.
- **C — deferred (mixed: non-physics + protected backend files in one commit):** 3.
- **Source-conflict — deferred (abort, not force-resolved):** 5.
- **Merge / empty — skipped:** `8e9ee656`, `d93d1b08` (merge, no content);
  `e2b1fcc0`, `8c460827` (already present on branch → empty).

## 3. Cherry-picked commits (23, B-class, applied with `-x`)

```
64c4be79 docs: define public-brand workbench design
a3540e0f docs: plan public-brand workbench rollout
c5580a2a chore: ignore local worktrees
5791d7bd feat(web): add public-brand workbench tokens
01590e3e feat(web): brand shared controls and modals
fee79c8e feat(web): unify workbench surfaces and motion
cb50064d feat(web): finish branded workbench controls
e288a855 perf(web): cache magnetic control bounds
b2f767b7 fix(web): preserve AA info contrast
b84cead5 fix(web): let manual theme override system preference
4aab5a64 build: refresh rebased Web release        (generated-file conflict → auto-resolved, see §5)
990cadd2 docs: make GitHub documentation English-first
8044a3cf docs(zh-CN): fix stale model default, add honest status section
1a17f79d docs(report): add Argus technical report 0.1 (source + compiled PDF)
6e2d80a3 docs: add Argus architecture illustration
87e8c629 docs: add Argus reliability illustration
7ef09864 docs(report): integrate accepted image-2 figures, drop TikZ schematics
974f271c docs: correct technical-report availability and scrub inspect-json local paths
1a9bd5ec fix(security): scrub local vault paths from figure sidecar provenance
11c8894d docs: define stronger public-brand workbench
f4faa6d2 docs: plan stronger branded workbench
209317e2 feat(web): adopt Rounded 02 workbench identity
8f384db7 feat(web): strengthen blue-gold glass depth
```
All are docs / web-branding / security-scrub — none touch `argus_skill/**/*.py`,
`manager/_core.py`, `loop.py`, `vertical_select.py`, `tools/stage_check.py`,
`verticals/physics/*`, `skills/research_gates.py`, or `skills/capability_registry.py`.

## 4. Deferred / mixed commits (NOT taken)

### C — mixed (non-physics + protected backend file in the SAME commit)
| sha | subject | protected file(s) touched |
|---|---|---|
| `297ca301` | fix(agent): stop high-activity nondecision loops | `argus_skill/loop.py` (+15 other) |
| `80641e48` | fix: harden and speed up Argus product surfaces | `argus_skill/manager/_core.py` (+37 other) |
| `bb4f6f1d` | fix(lifecycle): stabilize research completion contracts | `argus_skill/manager/_core.py`, `skills/vertical_select.py` (+38 other) |

Not whole-commit cherry-picked (would overwrite our repair-loop / gate wiring in
`_core.py`, `loop.py`, `vertical_select.py`). Recorded for later **manual, file-level**
extraction of only the non-physics, non-overlapping hunks if wanted.

### Source-conflict — deferred (`git cherry-pick --abort`, never force-resolved)
| sha | subject | conflicting file(s) | handling |
|---|---|---|---|
| `008512cd` | fix(reviewer): reject invalid progress classifications | `argus_skill/reviewer/_parsing.py`, `tests/test_reviewer_progress_class.py` | source conflict → abort + defer |
| `ff0a3eec` | build(release): refresh rebased frontend artifacts | `frontend/tui/bundle/argus.mjs`, `frontend/web/dist/**` | frontend build-artifact conflict → abort + defer |
| `a1ddee02` | feat(web): add blue-gold active-state hierarchy | `frontend/web/dist/assets/*.css/js` | build-artifact conflict → abort + defer |
| `cf0c904a` | fix(web): keep startup branding blue-gold | `frontend/web/dist/**`, `frontend/web/src/test/core.test.ts` | conflict → abort + defer |
| `a7e017e0` | build: refresh rebased branded release | `frontend/tui/bundle/argus.mjs`, `frontend/web/dist/**` | build-artifact conflict → abort + defer |

The four frontend ones conflict on pre-built `dist/` bundles that earlier picked web
commits already rewrote; they are pure rebuild refreshes (no backend impact) and can
be regenerated by the frontend build later. `008512cd` is a real reviewer-source
divergence — deferred per policy (no force-resolve on source conflicts).

## 5. Generated-file handling

- Only `4aab5a64` produced a generated-file-only conflict
  (`release_manifest.json` / `release.generated.ts`). Per policy these are not
  hand-merged: the incoming side was taken to let the pick continue, then **after all
  picks** the manifest was regenerated from source via
  `scripts/generate_release_manifest.py` and committed (`9d16c108`).
- Post-regen digest: `release_id 0.1.0+f2e5b21a25f92a11`. The digest changed vs backup
  **only because it also hashes `frontend/*/src/**/*.ts`**, which the intended B-class
  web-branding commits modified. **All `argus_skill/**/*.py` backend source is
  byte-for-byte identical to the backup** — no backend drift.

## 6. Tests (all green)

`env -u ARGUS_SKILL_VERTICAL -u ARGUS_SKILL_PHYSICS_CAPABILITY_LIB` →
- registry (`test_capability_registry.py`, incl. V3 adapter), research_gates
- physics gates: literature / theory / numerical / novelty / paper_type
- manuscript repair (`test_manuscript_repair.py`), manuscript & paper-style contracts
- `test_stage_decider.py`, `test_stage_check_physics_acceptance.py`
- **release** (`test_release.py`)

**219 passed, 0 failed.**

## 7. Integrity of the local gate stack

Verified byte-identical to `backup/physics-thin-mainline-sync-70888eaa`:
`skills/capability_registry.py` (V3 adapter), `skills/research_gates.py`,
`skills/manuscript_repair.py`, `verticals/physics/stages.py`,
`verticals/physics/manuscript.py`, `verticals/physics/gates/*`,
`manager/_core.py`, `loop.py`, `tools/stage_check.py`, `skills/vertical_select.py`.
→ **V3 adapter, research gates, and manuscript repair loop are fully intact.**

## 8. Go / no-go for the fresh Haldane mission

✅ **GO.** Backend untouched by the sync; all gate + repair + release tests pass; V3
registry loads 90/90/14 (re-confirmed in the post-sync smoke). No push, no merge, no
force reset. Backup branch retained for rollback.
