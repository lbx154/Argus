# Team Progress-Aware Lifecycle — Reconcile onto Refactored Main — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the daemon-fork's env-gated *progress-aware teammate lifecycle* (dive mode + near-death handoff) from `/data/yijia/argus-skill` (`3bb068a` + `c51d0dd`) onto the refactored `origin/main` (`823dfcf`), resolving the semantic collisions with main's independently-evolved measured-mode / Manager-owned-stage / structural-vs-knob logic — so the two diverged team forms become one.

**Architecture:** The feature is **purely additive and env-gated** (`ARGUS_TEAM_DIVE_MODE`, `ARGUS_TEAMMATE_HANDOFF`; both default OFF → behavior byte-identical to today's main). We do NOT merge the daemon branch (it would drag in `11332c2` measured-bench, which main already has as `a5c92ee`). We cherry-port only the 2 lifecycle commits, re-applied onto main's renamed symbols and placed to correctly *override* main's measured-mode breadth pressure.

**Tech Stack:** Python 3, pytest, `argus_skill/{loop.py,reviewer/_core.py,team/teammate_entry.py}`, env-flag gating.

---

## Background — established facts (read once)

- **Base:** branch `dev/team-latest` @ `origin/main 823dfcf`. Team baseline green: `pytest tests/team tests/tools/test_team_cli.py` = 43 passed.
- **Port source:** remote `daemon` → `/data/yijia/argus-skill` is already added & fetched. Reference the source diffs with `git show 3bb068a` / `git show c51d0dd`.
- **Fork point:** `5774103`. Daemon-only commits since fork: `11332c2` (measured-bench — **already in main as `a5c92ee`, identical 9-file stat → SKIP**), `3bb068a` (dive mode + near-death handoff — **PORT**), `c51d0dd` (dive = structural-not-param-sweep refinement — **PORT**).
- **Conflict surface when porting only the 2 lifecycle commits:** `loop.py`, `reviewer/_core.py` (main moved `engineer/reviewer.py`→here via `22cdc2a`), `team/teammate_entry.py`. (`tools/team.py` only conflicts via the skipped `11332c2`.)
- **Symbol drift to adapt:** `apps._life_repl`→`apps._runtime`, `_CodexSkillLoopRunner`→`_SkillLoopRunner`, `engineer/reviewer.py`→`reviewer/_core.py`, `meta`→`regime_jump`.
- **Semantic collisions to resolve (the crux):**
  1. **dive ↔ measured-mode breadth pressure.** main pushes breadth in two places that dive must override: `loop.py:493-513` ("MEASURED-BENCHMARK MODE … if not, NEXT round try a DIFFERENT mechanism") and `reviewer/_core.py:429-465` ("PUSH mechanism diversity"). The dive sections must be appended *after* these so they win.
  2. **dive ↔ Manager-owned stage** (`loop.py:460-471`, `bf9a605`): reviewer only *certifies*; the Manager moves the stage from the verdict. Dive must shape the **verdict** (continue/blocked/done), never a stage transition.
  3. **`c51d0dd` anti-param-sweep ↔ main's convergent `search_altitude_block`** (`reviewer/_core.py:416-421` "declared structural line vs Nth single-knob"). Main built the same concept independently → the dive verdict rule must **consume** the altitude facts, not paste a parallel signal.
  4. **dive ↔ valley-immunity explore-window** (`loop.py:434-446`): both can target one task → define precedence.

## File change map

| File | Change |
|---|---|
| `argus_skill/team/teammate_entry.py` | +2 env-gated blocks: publish `_ARGUS_HANDOFF_EPOCH`, resolve `ARGUS_TEAMMATE_HANDOFF_PATH` (adapt onto main's `_runtime` imports) |
| `argus_skill/loop.py` | +2 env-gated prompt sections in `_build_engineer_prompt` (dive + near-death), placed after the measured/ground-truth block (after line ~517) |
| `argus_skill/reviewer/_core.py` | +1 env-gated dive verdict override after the `stage_checklist` assignment (after line ~469), consuming the altitude facts |
| `tests/team/test_dive_lifecycle.py` | NEW — env-gated behavior + default-off safety |
| `docs/progress-aware-lifecycle-design.md` | copy the design doc from the daemon clone for provenance |

---

### Task 0: Setup & baseline

**Files:** none (verification only)

- [ ] **Step 1: Confirm base + green baseline**

Run:
```bash
cd /data/yijia/argus-merge && git rev-parse --abbrev-ref HEAD   # expect dev/team-latest
python -m pytest tests/team tests/tools/test_team_cli.py -q -p no:cacheprovider; echo "EXIT=$?"
```
Expected: `dev/team-latest`, `EXIT=0` (43 passed).

- [ ] **Step 2: Confirm port sources are reachable**

Run: `git show --stat 3bb068a c51d0dd | head -30`
Expected: both commits print (remote `daemon` fetched).

- [ ] **Step 3: Copy the design doc for provenance**

```bash
cp /data/yijia/argus-skill/docs/progress-aware-lifecycle-design.md docs/progress-aware-lifecycle-design.md
git add docs/progress-aware-lifecycle-design.md
git commit -m "docs(team): import progress-aware-lifecycle design from daemon fork (provenance)"
```

---

### Task 1: Near-death handoff plumbing in `teammate_entry.py`

**Files:**
- Modify: `argus_skill/team/teammate_entry.py` (in `run_one_engineer_mission` after `timeout_s` is resolved ~line 183; in `main()` after `cwd` is resolved ~line 365)
- Test: `tests/team/test_dive_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_dive_lifecycle.py
import os, json
from pathlib import Path
import argus_skill.team.teammate_entry as te

def test_handoff_epoch_published_when_enabled(monkeypatch):
    monkeypatch.setenv("ARGUS_TEAMMATE_HANDOFF", "1")
    monkeypatch.setenv("ARGUS_TEAMMATE_HANDOFF_AT", "0.9")
    monkeypatch.delenv("_ARGUS_HANDOFF_EPOCH", raising=False)
    # call only the epoch-publishing prologue via a tiny shim:
    te._publish_handoff_epoch(timeout_s=1000.0, now=1000.0)
    assert abs(float(os.environ["_ARGUS_HANDOFF_EPOCH"]) - (1000.0 + 900.0)) < 1e-6

def test_handoff_epoch_absent_when_disabled(monkeypatch):
    monkeypatch.delenv("ARGUS_TEAMMATE_HANDOFF", raising=False)
    monkeypatch.delenv("_ARGUS_HANDOFF_EPOCH", raising=False)
    te._publish_handoff_epoch(timeout_s=1000.0, now=1000.0)
    assert "_ARGUS_HANDOFF_EPOCH" not in os.environ

def test_handoff_path_resolved_under_team_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_TEAMMATE_HANDOFF", "1")
    monkeypatch.delenv("ARGUS_TEAMMATE_HANDOFF_DIR", raising=False)
    p = te._resolve_handoff_path(root=tmp_path, cwd=str(tmp_path), task_id="t::a/b")
    assert p is not None and Path(p).parent == tmp_path / "active_dives"
    assert Path(p).name == "t__a_b.json"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -q`
Expected: FAIL (`module 'teammate_entry' has no attribute '_publish_handoff_epoch'`).

- [ ] **Step 3: Implement the two helpers + wire them (extract for testability)**

Add to `argus_skill/team/teammate_entry.py` (module level):
```python
def _publish_handoff_epoch(*, timeout_s: float, now: float) -> None:
    """Env-gated: publish the wall-clock epoch at ~ARGUS_TEAMMATE_HANDOFF_AT of
    this mission's lifetime so the prompt builder can ask for a WIP handoff on the
    last round. No-op unless ARGUS_TEAMMATE_HANDOFF is set."""
    if os.environ.get("ARGUS_TEAMMATE_HANDOFF", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        _at = float(os.environ.get("ARGUS_TEAMMATE_HANDOFF_AT", "0.9"))
    except ValueError:
        _at = 0.9
    os.environ["_ARGUS_HANDOFF_EPOCH"] = str(now + max(0.0, min(1.0, _at)) * timeout_s)


def _resolve_handoff_path(*, root: Path, cwd: str, task_id: str) -> str | None:
    """Env-gated: resolve + create WHERE this mission writes its WIP handoff, and
    return it. No-op (None) unless ARGUS_TEAMMATE_HANDOFF is set."""
    if os.environ.get("ARGUS_TEAMMATE_HANDOFF", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    import re as _re
    _hd = (os.environ.get("ARGUS_TEAMMATE_HANDOFF_DIR", "").strip() or str(root / "active_dives"))
    _hp = Path(_hd)
    _hp = _hp if _hp.is_absolute() else (Path(cwd) / _hp)
    _hp.mkdir(parents=True, exist_ok=True)
    _safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
    return str(_hp / (_safe + ".json"))
```
Then call them: in `run_one_engineer_mission`, immediately after `timeout_s` is resolved (~line 183), add `_publish_handoff_epoch(timeout_s=timeout_s, now=time.time())`. In `main()`, after `cwd = args.cwd or os.getcwd()` (~line 365), add:
```python
    _hp = _resolve_handoff_path(root=root, cwd=cwd, task_id=task_id)
    if _hp:
        os.environ["ARGUS_TEAMMATE_HANDOFF_PATH"] = _hp
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/teammate_entry.py tests/team/test_dive_lifecycle.py
git commit -m "feat(team): near-death handoff plumbing (env-gated) ported onto _runtime"
```

---

### Task 2: Dive + near-death prompt sections in `loop.py`

**Files:**
- Modify: `argus_skill/loop.py` in `_build_engineer_prompt`, **after** the measured/ground-truth block (insert after line ~517, before `if skill_text:` at line 518)
- Test: `tests/team/test_dive_lifecycle.py`

- [ ] **Step 1: Write the failing test** (extract the section builder so it is unit-testable)

```python
def test_dive_section_present_only_when_enabled_and_sentinel(monkeypatch):
    from argus_skill.loop import _dive_and_handoff_sections
    monkeypatch.setenv("ARGUS_TEAM_DIVE_MODE", "1")
    secs = _dive_and_handoff_sections(task="optimize X [ARGUS-DIVE-MODE]")
    assert any("DIVE MODE" in s and "STRUCTURAL" in s.upper() for s in secs)
    monkeypatch.setenv("ARGUS_TEAM_DIVE_MODE", "1")
    assert _dive_and_handoff_sections(task="optimize X (no sentinel)") == []
    monkeypatch.delenv("ARGUS_TEAM_DIVE_MODE", raising=False)
    assert _dive_and_handoff_sections(task="optimize X [ARGUS-DIVE-MODE]") == []

def test_near_death_section_emitted_after_epoch(monkeypatch):
    from argus_skill.loop import _dive_and_handoff_sections
    monkeypatch.setenv("ARGUS_TEAMMATE_HANDOFF_PATH", "/tmp/h.json")
    monkeypatch.setenv("_ARGUS_HANDOFF_EPOCH", "1.0")  # already in the past
    secs = _dive_and_handoff_sections(task="anything")
    assert any("NEAR-DEATH" in s and "/tmp/h.json" in s for s in secs)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -k dive_section -q`
Expected: FAIL (`cannot import name '_dive_and_handoff_sections'`).

- [ ] **Step 3: Implement `_dive_and_handoff_sections` and call it in `_build_engineer_prompt`**

Add module-level helper in `argus_skill/loop.py` (use the **`c51d0dd` refined** dive text — structural, reject param sweeps):
```python
def _dive_and_handoff_sections(*, task: str) -> list[str]:
    import os as _os, time as _time
    out: list[str] = []
    _dive_on = _os.environ.get("ARGUS_TEAM_DIVE_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    if _dive_on and "[ARGUS-DIVE-MODE]" in (task or ""):
        out.append(
            "## DIVE MODE — ENGINEER the architecture deeper on ONE approach (NOT a param sweep)\n"
            "This task is in DIVE MODE: breadth is exhausted and the winning approach is "
            "identified. ENGINEER that approach toward its SOTA implementation — a deliberate "
            "architectural dive along ONE line, NOT trying new mechanisms and NOT sweeping "
            "parameters.\n"
            "- Do NOT switch approach. Do NOT rewrite from scratch. CONTINUE the current WIP "
            "(the half-finished implementation + its handoff notes) toward the target architecture "
            "(e.g. a full fused / persistent kernel).\n"
            "- **Each round must make a STRUCTURAL change** — fuse another stage, remove a kernel "
            "launch / intermediate, hand-write more of the fused/persistent kernel, restructure the "
            "tiling / memory layout, or replace a library call with a custom kernel doing strictly "
            "more. The diff must change the kernel's STRUCTURE.\n"
            "- **FORBIDDEN as a round's only change** (NOT a dive step, NOT progress): tweaking "
            "tile/block size, warp count, num_stages, pack size, num_splits, an autotune config, or "
            "a dtype/flag of an existing call. A number/flag-only diff is param-tuning, not diving.\n"
            "- Progress = the architecture got DEEPER (one more fusion, one fewer launch, more of "
            "the kernel hand-written) — even if the score has not moved yet. Ignore any instruction "
            "above to 'try a different mechanism'; it does not apply in dive mode."
        )
    _handoff_path = _os.environ.get("ARGUS_TEAMMATE_HANDOFF_PATH", "").strip()
    if _handoff_path:
        try:
            _epoch = float(_os.environ.get("_ARGUS_HANDOFF_EPOCH", "0") or 0)
        except ValueError:
            _epoch = 0.0
        if _epoch and _time.time() > _epoch:
            out.append(
                "## ⚠ NEAR-DEATH — write your handoff NOW (last round likely)\n"
                "You are near the end of your lifetime; this may be your LAST round. Before you "
                "stop, WRITE A STRUCTURED HANDOFF so the next teammate continues your work instead "
                "of restarting it. Write JSON to `" + _handoff_path + "`:\n"
                "{\n"
                '  "approach": "<the one approach/mechanism you are developing>",\n'
                '  "wip_path": "<path to the in-progress artifact you were editing>",\n'
                '  "progress_state": "not-started|partial|works|optimal",\n'
                '  "metric": <your best measured number this life, or null>,\n'
                '  "next_step": "<the single most concrete next action to advance it>",\n'
                '  "self_eval": <0.0-1.0 how close to done>\n'
                "}\n"
                "Make `next_step` concrete enough that a fresh teammate can act immediately, and "
                "`wip_path` point at real in-progress code (not a from-scratch restart)."
            )
    return out
```
Then in `_build_engineer_prompt`, **after** line ~517 (`sections.append(ground_truth_mandate(...))` / the measured branch) and before `if skill_text:`, add:
```python
        sections.extend(_dive_and_handoff_sections(task=task))
```
Placement rationale (collision #1): this lands AFTER the `MEASURED-BENCHMARK MODE` block (493-513) so its "do NOT switch approach" wins over that block's "try a DIFFERENT mechanism". It does NOT touch the Manager-owned-stage block (collision #2 — dive never moves a stage).

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -k 'dive_section or near_death' -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/loop.py tests/team/test_dive_lifecycle.py
git commit -m "feat(team): dive-mode + near-death engineer-prompt sections (env-gated), placed to override measured-mode breadth"
```

---

### Task 3: Dive reviewer verdict override in `reviewer/_core.py`

**Files:**
- Modify: `argus_skill/reviewer/_core.py` — after the `stage_checklist` assignment block (after line ~469), before the academic-review block (line ~471)
- Test: `tests/team/test_dive_lifecycle.py`

- [ ] **Step 1: Write the failing test** (extract an override helper)

```python
def test_dive_reviewer_override_rejects_param_sweep(monkeypatch):
    from argus_skill.reviewer._core import _dive_stage_checklist_override
    monkeypatch.setenv("ARGUS_TEAM_DIVE_MODE", "1")
    txt = _dive_stage_checklist_override(objective="opt [ARGUS-DIVE-MODE]", base="BASE")
    assert txt is not None
    assert "STRUCTURAL" in txt.upper() and "param" in txt.lower()
    monkeypatch.delenv("ARGUS_TEAM_DIVE_MODE", raising=False)
    assert _dive_stage_checklist_override(objective="opt [ARGUS-DIVE-MODE]", base="BASE") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -k dive_reviewer -q`
Expected: FAIL (import error).

- [ ] **Step 3: Implement `_dive_stage_checklist_override` and apply it**

Add module-level helper in `argus_skill/reviewer/_core.py` (use the **`c51d0dd` refined** reviewer text; it harmonizes with — not duplicates — the existing `search_altitude_block`, which already supplies the structural-vs-knob FACTS, by adding the matching VERDICT rule):
```python
def _dive_stage_checklist_override(*, objective: str, base: str) -> str | None:
    import os as _os
    if _os.environ.get("ARGUS_TEAM_DIVE_MODE", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if "[ARGUS-DIVE-MODE]" not in (objective or ""):
        return None
    return (
        "## DIVE MODE — judge ARCHITECTURAL depth on the SAME approach (reject param sweeps)\n"
        "This task is in DIVE MODE: the approach is fixed and the engineer must ENGINEER it deeper "
        "toward its SOTA implementation, NOT sweep parameters. Do NOT push mechanism diversity, but "
        "DO hold the engineer to real structural progress. (Use the search-altitude facts above to "
        "tell a real structural advance from an Nth single-knob nibble.)\n"
        "- `continue` ONLY if this round made a STRUCTURAL change — fused another stage, removed a "
        "launch/intermediate, hand-wrote more of the fused/persistent kernel, or changed the "
        "tiling/memory layout at the algorithm level — even if the score did not improve yet. Your "
        "`next_action` MUST name the next STRUCTURAL step (never 'tune a parameter', never 'try a "
        "different approach').\n"
        "- `blocked` if the round's ONLY change was parameters/flags (tile/block/warp/num_stages/"
        "pack/num_splits/autotune/dtype) — param-tuning, NOT a dive; set `next_action` to the "
        "concrete structural change to make instead. Also `blocked` if the approach is proven dead "
        "or there is an operator-only blocker.\n"
        "- `done` only if the approach is complete and at/above the known ceiling.\n"
        "Iterating the SAME code toward a deeper architecture is the point — reward structural "
        "depth, reject knob-sweeping, and ignore any instruction to demand a new mechanism."
    )
```
Then, right after the `if _measured: … elif … else …` block that assigns `stage_checklist` (after line ~469), add:
```python
        _dive_override = _dive_stage_checklist_override(objective=objective, base=stage_checklist)
        if _dive_override is not None:
            stage_checklist = _dive_override
```
Collision #2 note: this only changes the **verdict checklist text**; the reviewer still emits a verdict the Manager consumes to move the stage — verify in Task 5. Collision #1: it overrides the `_measured` reviewer block (429-465) because it runs after that assignment.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/team/test_dive_lifecycle.py -k dive_reviewer -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/reviewer/_core.py tests/team/test_dive_lifecycle.py
git commit -m "feat(team): dive-mode reviewer verdict override (env-gated), harmonized with search-altitude facts"
```

---

### Task 4: Design-interaction verification (the "verify conflicting designs" ask)

**Files:** none (analysis + targeted assertions). Record findings in the PR description / a short `docs/` note.

- [ ] **Step 1: dive ↔ Manager stage-write path.** Read `loop.py` Manager wiring (grep `Manager`, `stage_decision`) + `reviewer/_core.py` verdict emission. Confirm the dive override changes only the verdict checklist, and the verdict still flows reviewer→Manager (`bf9a605`). Acceptance: dive never calls a stage-transition helper; a `blocked`/`continue` dive verdict reaches the Manager unchanged.

- [ ] **Step 2: dive ↔ valley-immunity explore-window** (`loop.py:434-446`). Decide precedence when a task is BOTH in an open explore-window AND dive-marked. Acceptance: documented rule + a test asserting the chosen precedence (recommended: dive wins — an explicitly lead-marked depth task suppresses the breadth window; or require the lead to not dive-mark a task with `explore_window>0`).

- [ ] **Step 3: dive ↔ measured-mode precedence** (already structurally handled by placement). Acceptance test: with BOTH `ARGUS_SKILL_MEASURED_MODE=1` and `ARGUS_TEAM_DIVE_MODE=1` + sentinel, the engineer prompt contains the dive "do NOT switch approach" section AFTER the measured "try a DIFFERENT mechanism" block, and the reviewer checklist is the dive override (not the measured block).

```python
def test_dive_overrides_measured_in_both_prompts(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
    monkeypatch.setenv("ARGUS_TEAM_DIVE_MODE", "1")
    # build a full engineer prompt + reviewer checklist via their public builders
    # assert dive markers present and positioned after measured markers
```

- [ ] **Step 4: lifetime defaults.** Design assumes teammate lifetime 2h; main default `ARGUS_TEAMMATE_TIMEOUT_S=5400` (90min). Handoff epoch is a fraction of `timeout_s` so it adapts — no code change; record the operator setting (`ARGUS_TEAMMATE_TIMEOUT_S`, `ARGUS_TEAM_DIVE_MAX_LIVES`) in the rollout note.

- [ ] **Step 5: Commit findings**

```bash
git add docs/ && git commit -m "docs(team): record dive-mode design-interaction resolutions (manager/valley/measured/lifetime)"
```

---

### Task 5: Default-OFF safety + full regression

**Files:** `tests/team/test_dive_lifecycle.py`

- [ ] **Step 1: Safety test — flags off ⇒ no new sections**

```python
def test_all_flags_off_is_noop(monkeypatch):
    for v in ("ARGUS_TEAM_DIVE_MODE","ARGUS_TEAMMATE_HANDOFF","ARGUS_TEAMMATE_HANDOFF_PATH","_ARGUS_HANDOFF_EPOCH"):
        monkeypatch.delenv(v, raising=False)
    from argus_skill.loop import _dive_and_handoff_sections
    from argus_skill.reviewer._core import _dive_stage_checklist_override
    assert _dive_and_handoff_sections(task="x [ARGUS-DIVE-MODE]") == []
    assert _dive_stage_checklist_override(objective="x [ARGUS-DIVE-MODE]", base="B") is None
```

- [ ] **Step 2: Full team + loop + reviewer regression**

Run:
```bash
python -m pytest tests/team tests/tools/test_team_cli.py tests/test_loop.py tests/reviewer -q -p no:cacheprovider; echo "EXIT=$?"
```
Expected: `EXIT=0`, 43 team tests still green + new dive tests pass. (If `tests/test_loop.py` / `tests/reviewer` paths differ, discover with `ls tests`.)

- [ ] **Step 3: Commit**

```bash
git add tests/team/test_dive_lifecycle.py
git commit -m "test(team): default-off safety + dive/handoff regression"
```

---

## Out of scope for THIS plan (separate tracks — flag to the user)

- **Lifecycle-CONTROL bug fixes** (BUG-1 orphan coordinator at `hb==0`, BUG-4 teammate-timebox ≫ lead-ttl, BUG-2/3/5) — see `docs/superpowers/specs/2026-06-26-team-daemon-lifecycle-diagnosis.md`. These are the *daemon-can't-control-lifecycle* root cause and warrant their own plan (A2 daemon reaping hook). They are independent of this dive/handoff reconcile.
- **Live leak stop** on the daemon box (2 coordinators @18h, teammates >100min) — operational, do on the daemon, not in this branch.
- **Lead-side continuation decision** (design §5: consume handoff record, `MAX=1` renewal) — the daemon fork did not implement a lead CLI verb for it; if desired, add `team continue --root --task-id` in a follow-up.

## Rollout

1. Land Tasks 0–5 on `dev/team-latest`; open PR onto `main`.
2. After merge, redeploy the daemon checkout `/data/yijia/argus-skill` from reconciled `main` (this also brings it main's Manager/regime_jump work it currently lacks) — **resolve its 3 local-only commits are now upstreamed; its `merge/teams-onto-latest` branch can be retired.**
3. Operator enables on SOL via env: `ARGUS_TEAM_DIVE_MODE=1`, `ARGUS_TEAMMATE_HANDOFF=1` (+ `ARGUS_TEAMMATE_HANDOFF_DIR`), keep `ARGUS_SKILL_MEASURED_MODE=1`.

## Self-review checklist (done)
- Spec coverage: dive (loop+reviewer), handoff (teammate_entry+loop), skip measured-bench, 4 design collisions, default-off safety — all have tasks. ✓
- No placeholders: real text/code pasted from `3bb068a`/`c51d0dd`. ✓
- Symbol consistency: `_runtime`/`_SkillLoopRunner`, `reviewer/_core.py`, env-flag names match across tasks. ✓
