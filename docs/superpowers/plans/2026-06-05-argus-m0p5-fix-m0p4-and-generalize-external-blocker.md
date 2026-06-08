# Argus M0.5 — fix M0.4 boolean inversion + generalize external-blocker short-circuit

> **Executor:** Codex via tmux + `codex --yolo` + `/goal <this file>` in the
> existing `wiki-m0-codex` session.
> Architect (Claude) hands this; he verifies.

## Why this exists

Two findings from the codex architectural audit on 2026-06-05 (see
`docs/superpowers/plans/2026-06-05-argus-m0p4-bounded-disables-emnlp-gate.md`
for prior context):

1. **M0.4 boolean is INVERTED** at `daemon/life_worker.py:858` and
   `apps/_life_repl.py:1378`. Current expression `full_emnlp_gate=not
   cfg.continuous_open_ended` evaluates to:
   - `--bounded` (open_ended=False) → gate=True ← WRONG, should be False
   - non-bounded (open_ended=True)   → gate=False ← WRONG, should be True
   The bug is latent because the stage_check escalation hits first; once
   we fix the escalation (Task 2-3), the EMNLP loop returns.

2. **Planner's "any gate fail → enqueue repair task" assumption** is the
   root cause of the recurring loops. The pattern is encoded in 4+ sites
   (`planner.py:164,256,566`, `supervisor.py:2313`). Argus's two prior
   self-patches (`398b9d0`, `3c40efa`) hardcoded dated filenames to
   bypass it for one specific scope and one specific lock file. M0.5
   ships the **cheap fix** from codex's audit: supervisor-level
   short-circuit that generalizes `3c40efa`, and removal of `398b9d0`'s
   project-specific hack.

Out of scope: principled refactor (gate actionability/owner
classification) and schema changes (`PlannerVerdict.blocked_external`).
Those are M0.6+ if cheap fix proves insufficient.

---

## File structure

**Modified:**
| Path | Change |
|---|---|
| `argus_skill/daemon/life_worker.py` | line 858 — remove the stray `not` |
| `argus_skill/apps/_life_repl.py` | line 1378 — remove the stray `not` |
| `argus_skill/life/supervisor.py` | (a) `_operator_only_external_blocker_wait_reason` (line ~2024): glob `diagnosis/operator_only_external_blocker_*.json` instead of hardcoded date filename; (b) NEW pre-planner short-circuit method that emits `waiting=true` when an external-blocker artifact exists |
| `argus_skill/tools/stage_check.py` | REMOVE `_is_bounded_reward_survey_scope` and `_bounded_reward_survey_findings` (the `398b9d0` hardcoded survey filename hack). Keep the rest of `a7544c3` stage_check hardening |
| `tests/test_bounded_disables_emnlp_gate.py` | Fix the test to assert the CORRECT direction (no `not`) |
| `tests/tools/test_stage_check_fail_closed.py` | Drop or rewrite the bounded-survey-specific tests from `398b9d0` |

**New:**
| Path | Purpose |
|---|---|
| `tests/life/test_external_blocker_short_circuit.py` | Generic external-blocker → waiting behavior |

---

## Task 1: Fix M0.4 inverted boolean (P0 — 1 char each in 2 files)

**Files:**
- Modify: `argus_skill/daemon/life_worker.py`
- Modify: `argus_skill/apps/_life_repl.py`
- Modify: `tests/test_bounded_disables_emnlp_gate.py`

### Step 1 — confirm CLI plumbing

```bash
grep -n "continuous_open_ended" argus_skill/apps/cli.py argus_skill/daemon/life_worker.py argus_skill/apps/_life_repl.py
```

Verify:
- `cli.py:1267` sets `continuous_open_ended=not bool(args.bounded)` (so
  `--bounded` ⇒ open_ended=False; default ⇒ open_ended=True).
- We want `full_emnlp_gate=cfg.continuous_open_ended` (the `not` from
  M0.4 is wrong: with `--bounded`, open_ended=False, and gate should
  also be False; the values match without negation).

### Step 2 — apply the 1-char fix

In `argus_skill/daemon/life_worker.py` around line 858, change:
```python
            full_emnlp_gate=not cfg.continuous_open_ended,
```
to:
```python
            # M0.5: M0.4 had this inverted. continuous_open_ended already
            # encodes "True when NOT --bounded"; that is the same polarity
            # as full_emnlp_gate ("True when paper-pipeline gating should
            # fire"). No negation needed.
            full_emnlp_gate=cfg.continuous_open_ended,
```

Apply the IDENTICAL change in `argus_skill/apps/_life_repl.py` around
line 1378 (whatever the local variable holding the bounded flag is —
the assignment should ultimately track `not bounded`).

### Step 3 — fix the test

In `tests/test_bounded_disables_emnlp_gate.py`, the regex assertions
should now look for the CORRECT (no `not`) expression:
```python
    assert (
        "full_emnlp_gate=cfg.continuous_open_ended" in src
        or "full_emnlp_gate=(cfg.continuous_open_ended)" in src
    ), (
        "life_worker.py must derive full_emnlp_gate as "
        "`cfg.continuous_open_ended` (M0.4 had this inverted; "
        "see M0.5 plan)"
    )
```

Add a comment at the top of the test file:
```python
# Regression test for the M0.5 boolean-inversion fix on top of M0.4.
# The expression must be `full_emnlp_gate=cfg.continuous_open_ended`
# (NO `not`).
```

### Step 4 — run

```bash
pytest tests/test_bounded_disables_emnlp_gate.py -v
```
Expected: 2 passed.

### Step 5 — commit

```bash
git add argus_skill/daemon/life_worker.py argus_skill/apps/_life_repl.py \
        tests/test_bounded_disables_emnlp_gate.py
git commit -m "supervisor: fix M0.4 inverted boolean (was: bounded enabled EMNLP gate)"
```

---

## Task 2: Generalize external-blocker glob (TDD)

**Files:**
- Modify: `argus_skill/life/supervisor.py` —
  `_operator_only_external_blocker_wait_reason` (~line 2024)
- Create: `tests/life/test_external_blocker_short_circuit.py`

### Step 1 — write the failing test

Create `tests/life/test_external_blocker_short_circuit.py`:
```python
"""External-blocker artifact discovery — generic glob, no dated filenames."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.supervisor import _operator_only_blocker_paths_for_project


def _write_blocker(project_root: Path, filename: str, payload: dict) -> Path:
    diagnosis = project_root / "diagnosis"
    diagnosis.mkdir(parents=True, exist_ok=True)
    path = diagnosis / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_finds_legacy_dated_lock_file(tmp_path: Path):
    """Backwards-compat: the 3c40efa-era dated filename should still match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_lock_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "operator_only_external_blocker_lock_20260605.json"


def test_finds_undated_generic_filename(tmp_path: Path):
    """Forward-compat: new generic filename without date should also match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1


def test_returns_empty_when_no_blocker_file(tmp_path: Path):
    (tmp_path / "diagnosis").mkdir()
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_ignores_unrelated_diagnosis_files(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "stage_check_terminal_index.md").write_text("ignore me")
    (diagnosis / "operator_action_required.md").write_text("ignore me")
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_picks_most_recent_when_multiple(tmp_path: Path):
    import time
    p1 = _write_blocker(
        tmp_path, "operator_only_external_blocker_20260601.json",
        {"local_engineer_action_required_before_mount": False,
         "required_external_targets": ["a"]},
    )
    time.sleep(0.01)
    p2 = _write_blocker(
        tmp_path, "operator_only_external_blocker_20260605.json",
        {"local_engineer_action_required_before_mount": False,
         "required_external_targets": ["b"]},
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    # Most recent first.
    assert paths[0] == p2
```

### Step 2 — run to confirm failure

```bash
pytest tests/life/test_external_blocker_short_circuit.py -v
```
Expected: ImportError on `_operator_only_blocker_paths_for_project`.

### Step 3 — implement the glob helper

In `argus_skill/life/supervisor.py`, ADD a module-level helper near the
existing `_operator_only_external_blocker_wait_reason` method (around
line 2020):

```python
def _operator_only_blocker_paths_for_project(project_root: Path) -> list[Path]:
    """Return existing operator-only external-blocker artifact paths.

    Looks for ``diagnosis/operator_only_external_blocker_*.json`` (glob)
    AND the legacy un-dated ``diagnosis/operator_only_external_blocker.json``.
    Returned newest first by mtime; empty list when none.

    Generalizes the date-hardcoded path from commit 3c40efa so the
    behavior survives new runs and projects.
    """
    diagnosis = project_root / "diagnosis"
    if not diagnosis.is_dir():
        return []
    candidates: list[Path] = []
    for path in diagnosis.glob("operator_only_external_blocker*.json"):
        if not path.is_file():
            continue
        candidates.append(path)
    # Newest first by mtime; cheap and stable.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates
```

Then REPLACE the body of `_operator_only_external_blocker_wait_reason`
(line ~2024) to use the helper:

```python
    def _operator_only_external_blocker_wait_reason(self) -> str:
        """Return a waiting reason for an operator-only external blocker.

        Generic: scans for any ``diagnosis/operator_only_external_blocker_*.json``
        artifact, validates that local engineering is exhausted, and returns
        a human reason string. Empty string when nothing matches OR when the
        blocker indicates local action is still required.
        """
        project_root = self._project_workdir()
        for lock_path in _operator_only_blocker_paths_for_project(project_root):
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("local_engineer_action_required_before_mount") is not False:
                continue  # local work remains -> this is not an operator-only blocker
            required = payload.get("required_external_targets")
            if not isinstance(required, list) or not required:
                continue
            present = [
                str(item)
                for item in required
                if isinstance(item, str) and (project_root / item).exists()
            ]
            if present:
                continue  # at least one target arrived -> not blocked anymore
            missing_count = sum(1 for item in required if isinstance(item, str))
            owner = payload.get("next_owner") or "operator/data owner"
            verdict = payload.get("canonical_viability_verdict") or "external artifacts missing"
            return (
                f"operator-only external benchmark blocker ({lock_path.name}): "
                f"{verdict}; {missing_count} required external target(s) still "
                f"absent; next owner is {owner}"
            )
        return ""
```

### Step 4 — run tests

```bash
pytest tests/life/test_external_blocker_short_circuit.py -v
```
Expected: 5 passed.

### Step 5 — commit

```bash
git add argus_skill/life/supervisor.py tests/life/test_external_blocker_short_circuit.py
git commit -m "supervisor: external-blocker glob (was: date-hardcoded path from 3c40efa)"
```

---

## Task 3: Pre-planner short-circuit (TDD)

**Goal:** when an operator-only external blocker is present, supervisor
emits `waiting=True` and **does not call the planner at all** for this
cycle. This prevents `planner.py:256` ("if unsure, default to one bounded
current-stage gate mission") from re-enqueueing repair work.

**Files:**
- Modify: `argus_skill/life/supervisor.py` — wherever `LifeSupervisor`
  decides whether to call the planner (search for `plan_next` or
  `_plan_next_work`).
- Extend: `tests/life/test_external_blocker_short_circuit.py`

### Step 1 — discover the call site

```bash
grep -n "plan_next\|self.planner\|planner.plan_next" argus_skill/life/supervisor.py | head -10
```
Find the method that drives one planning cycle (likely
`LifeSupervisor._plan_next_work` or `_planning_tick`). The new check
should run BEFORE that call and short-circuit if a blocker is present.

### Step 2 — add the failing test

Append to `tests/life/test_external_blocker_short_circuit.py`:
```python
def test_short_circuit_emits_waiting_without_calling_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If an external blocker artifact is present, supervisor must NOT
    call planner.plan_next this cycle; instead emit a 'waiting' decision."""
    monkeypatch.chdir(tmp_path)
    _write_blocker(
        tmp_path, "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )
    # Construct a minimal supervisor and verify the pre-planner check.
    # Use the public helper rather than calling the planner directly.
    from argus_skill.life.supervisor import LifeSupervisor
    short_circuit = LifeSupervisor._operator_external_blocker_short_circuit_decision(
        project_root=tmp_path,
    )
    assert short_circuit is not None
    assert getattr(short_circuit, "waiting", False) is True
    assert "operator-only" in (
        getattr(short_circuit, "waiting_reason", "")
        or getattr(short_circuit, "reason", "")
    )
    assert getattr(short_circuit, "task_count", 0) == 0


def test_short_circuit_returns_none_without_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    from argus_skill.life.supervisor import LifeSupervisor
    assert LifeSupervisor._operator_external_blocker_short_circuit_decision(
        project_root=tmp_path,
    ) is None
```

### Step 3 — run to confirm failure

```bash
pytest tests/life/test_external_blocker_short_circuit.py -v
```
Expected: the new 2 tests fail on missing
`_operator_external_blocker_short_circuit_decision`.

### Step 4 — implement

In `argus_skill/life/supervisor.py`, add a staticmethod
`_operator_external_blocker_short_circuit_decision` to `LifeSupervisor`
that returns a `PlannerVerdict`-like waiting decision (use the same
dataclass the planner returns) OR `None`:

```python
    @staticmethod
    def _operator_external_blocker_short_circuit_decision(
        *, project_root: Path,
    ) -> Any | None:
        """Return a waiting verdict when an operator-only external blocker
        is present; None otherwise.

        Used by the planning cycle to short-circuit BEFORE the LLM-driven
        planner is called, preventing the planner from re-enqueueing
        impossible 'repair external artifact' missions.
        """
        for lock_path in _operator_only_blocker_paths_for_project(project_root):
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("local_engineer_action_required_before_mount") is not False:
                continue
            required = payload.get("required_external_targets")
            if not isinstance(required, list) or not required:
                continue
            present = [
                str(item)
                for item in required
                if isinstance(item, str) and (project_root / item).exists()
            ]
            if present:
                continue
            reason = (
                f"operator-only external blocker present ({lock_path.name}); "
                f"skipping planner cycle to avoid impossible repair-task loop"
            )
            from ..planner.planner import PlannerVerdict
            return PlannerVerdict(
                project_done=False,
                reason=reason,
                waiting=True,
                waiting_reason=reason,
                task_count=0,
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                skipped_recent_failure_tasks=0,
                enqueued_titles=[],
                enqueued_impact_scores=[],
                skipped_duplicate_titles=[],
                skipped_recent_failure_titles=[],
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                restart_daemon=False,
                restart_reason="",
            )
        return None
```

(Adjust field names to match the actual `PlannerVerdict` dataclass —
check `argus_skill/planner/planner.py` for the precise fields.)

Then wire it into the planning cycle: in `_plan_next_work` (or whichever
method calls `self.planner.plan_next(...)`), BEFORE that call, add:

```python
        short_circuit = self._operator_external_blocker_short_circuit_decision(
            project_root=self._project_workdir(),
        )
        if short_circuit is not None:
            # Emit a planner.verdict event so the journal/telemetry sees this,
            # then return without spending an LLM call.
            self._record_planner_verdict(short_circuit)  # use whatever
            # existing helper records verdicts; if none exists, emit the same
            # journal entry the regular path does.
            return short_circuit
```

If `_record_planner_verdict` doesn't exist by that name, find the
equivalent (search for `life.planner.verdict`).

### Step 5 — run tests

```bash
pytest tests/life/test_external_blocker_short_circuit.py -v
```
Expected: all 7 tests pass.

### Step 6 — commit

```bash
git add argus_skill/life/supervisor.py tests/life/test_external_blocker_short_circuit.py
git commit -m "supervisor: pre-planner short-circuit when operator-only external blocker present"
```

---

## Task 4: Remove the 398b9d0 hardcoded survey hack

**Files:**
- Modify: `argus_skill/tools/stage_check.py` — remove
  `_is_bounded_reward_survey_scope` and `_bounded_reward_survey_findings`
  and their call sites
- Modify: `tests/tools/test_stage_check_fail_closed.py` — drop tests
  asserting the project-specific behavior

### Step 1 — identify the dead code

```bash
grep -n "_is_bounded_reward_survey_scope\|_bounded_reward_survey_findings\|bounded_train_free_reward_survey\|process_terminal_reward_survey_20260605" argus_skill/tools/stage_check.py tests/tools/test_stage_check_fail_closed.py
```

### Step 2 — remove

In `argus_skill/tools/stage_check.py`, delete:
- `_is_bounded_reward_survey_scope(...)` function (added by 398b9d0)
- `_bounded_reward_survey_findings(...)` function (added by 398b9d0)
- All call sites that consult them (search for the function names).
- Any code blocks that branched on `scope == "bounded_train_free_reward_survey"`.

Keep the rest of the file (the generic stage_check hardening from
`a7544c3`).

In `tests/tools/test_stage_check_fail_closed.py`, remove or rewrite any
test that explicitly references the bounded-survey scope or the dated
report filename. Keep tests that exercise the generic gate logic.

### Step 3 — run the affected tests

```bash
pytest tests/tools/test_stage_check_fail_closed.py -v
```
Expected: green. If a test must be deleted (because it explicitly
asserted the now-removed bypass), delete it and note in the commit.

### Step 4 — commit

```bash
git add argus_skill/tools/stage_check.py tests/tools/test_stage_check_fail_closed.py
git commit -m "stage_check: remove 398b9d0 project-specific bypass (supervisor handles it)"
```

---

## Task 5: Full test sweep

```bash
pytest tests/test_bounded_disables_emnlp_gate.py \
       tests/life/test_external_blocker_short_circuit.py \
       tests/tools/test_stage_check_fail_closed.py \
       tests/test_wiki_*.py \
       tests/life/ \
       tests/planner/ -v 2>&1 | tail -30
```
Expected: all green. If a pre-existing test breaks, investigate before
moving on; do not green-wash.

---

## Task 6: Restart argus on unify_RL_argus and validate

The previous daemon was stopped by the architect. The wiki has 25
sources/papers, 12 pages/techniques, 6 sources/runs from prior runs.
We're testing whether M0.5 eliminates the escalation loop AND lets the
wiki keep growing.

- [ ] **Step 1: Confirm stopped**

```bash
cd /data/yijia/unify_RL_argus
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=$PWD/.argus_special_prompts argus-skill --status | grep daemon
# expect: daemon : not running
```

- [ ] **Step 2: Write a fresh operator-only blocker file so M0.5 has something to short-circuit on**

This simulates the state argus's engineer normally writes during a
mission. We pre-populate to validate that supervisor short-circuits
WITHOUT having to wait for the engineer to discover the blocker again.

```bash
mkdir -p /data/yijia/unify_RL_argus/diagnosis
cat > /data/yijia/unify_RL_argus/diagnosis/operator_only_external_blocker_20260605.json <<'EOF'
{
  "local_engineer_action_required_before_mount": false,
  "required_external_targets": [
    "local_eval_ckpts/",
    "models/",
    "hf_home/"
  ],
  "canonical_viability_verdict": "blocked: model checkpoints and HF cache intentionally excluded from sandbox copy",
  "next_owner": "operator",
  "discovered_at": "2026-06-05"
}
EOF
```

- [ ] **Step 3: Restart with the same objective as before**

```bash
OBJECTIVE='Survey the design space of process-reward + terminal-reward decompositions for image-editing RL. Step 1: read 503goal.md (archived at _argus_archive_20260605_run1/) to understand the project intended Subgoal Progress GRPO design. Step 2: find 5-7 recent papers (2023-2026) on process+outcome reward decomposition. For each paper extract: decomposition formula, key tradeoffs, comparison vs Bagel at src/unify_rl/. Step 3: produce reports/process_terminal_reward_survey_20260605.md with comparison table, 3-5 candidate Bagel-design improvements ranked by tractability, populate .autors/unify_RL_argus/wiki/ with each cited paper. Consult the wiki query_pack first. Train-free: no GPU.'

tmux send-keys -t argus-unify-rl 'cd /data/yijia/unify_RL_argus' Enter
tmux send-keys -t argus-unify-rl "ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --daemon-fg --continuous --bounded --objective \"$OBJECTIVE\"" Enter
sleep 30
tmux capture-pane -t argus-unify-rl -p | tail -20
```
Expected: daemon ready, but supervisor should LOG a short-circuit
decision on cycle 0 (look for "operator-only external blocker present"
in the log).

- [ ] **Step 4: Wait 3 minutes, snapshot mission types**

```bash
sleep 180
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --status 2>&1 | grep -E "history|cost|current" | head -5

EVENTS=/home/yifanyang/.argus-skill/projects/59ec632ebc50/events.jsonl
echo "Recent enqueued titles (last 5):"
grep "enqueued_titles" $EVENTS | tail -5 | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        d=json.loads(line)
        ts=d.get('ts','')
        print(f'  ts={ts}  titles={d.get(\"enqueued_titles\", [])}')
    except: pass
"

echo
echo "Short-circuit count (verdicts since restart):"
grep "operator-only external blocker present" $EVENTS | wc -l
```

Pass criteria:
- Recent enqueued_titles should NOT contain "Prove final submission readiness"
- Recent enqueued_titles should NOT contain "Repair benchmark external-artifact handoff files"
- The short-circuit count > 0 (supervisor did detect the blocker)
- Daemon is alive but mostly idle (no flurry of new missions)

- [ ] **Step 5: Remove the blocker artifact and see argus resume**

```bash
rm /data/yijia/unify_RL_argus/diagnosis/operator_only_external_blocker_20260605.json
sleep 60
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --status 2>&1 | grep -E "current|history|state" | head -5
```
Expected: planner resumes; either enqueues a real research task OR (if
the survey is already considered done) the wiki_collect cooldown is
checked and possibly a wiki_collect mission gets scheduled.

- [ ] **Step 6: Wiki status check**

```bash
W=/data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki
echo "sources/papers: $(ls $W/sources/papers/ | wc -l)"
echo "sources/repos:  $(ls $W/sources/repos/ | wc -l)"
echo "sources/runs:   $(ls $W/sources/runs/ | wc -l)"
echo "pages/techniques: $(ls $W/pages/techniques/ | wc -l)"
echo "pages/conflicts:  $(ls $W/pages/conflicts/ | wc -l)"
echo "pages/patterns:   $(ls $W/pages/patterns/ | wc -l)"
echo
echo "bot_state (wiki-collector last fired):"
cat $W/data/bot_state.json 2>/dev/null || echo "  (not present - collector hasn't run yet)"
```

- [ ] **Step 7: Report back**

Print a concise stdout summary:
- Test results from Task 5
- Restart in Task 6 step 3: daemon ready? short-circuit detected?
- After 3 minutes (Task 6 step 4): titles seen, short-circuit count
- After unblocking (Task 6 step 5): did planner resume?
- Wiki growth since restart (Task 6 step 6)
- Anything weird, especially anything that suggests M0.5 didn't catch

Do NOT babysit beyond Step 7. The architect verifies.

---

## Definition of done

- `tests/test_bounded_disables_emnlp_gate.py` passes WITHOUT `not` in the
  assertion (M0.4 inversion fix)
- `tests/life/test_external_blocker_short_circuit.py` passes (7 tests)
- `tests/tools/test_stage_check_fail_closed.py` still green after 398b9d0
  removal
- Full sweep no regressions
- Argus restart on unify_RL_argus shows:
  - Short-circuit verdict in events.jsonl
  - No "Prove final submission readiness" enqueued in first 3 min
  - No "Repair benchmark external-artifact handoff files" enqueued in
    first 3 min
- Removing the blocker file allows planner to resume

## Non-goals (M0.6+)

- Principled refactor: gate actionability/owner classification on
  stage_check artifacts and PipelineState
- Schema-changing fix: `PlannerVerdict.blocked_external` structured
  output, reviewer `planner_report` structured blocker field
- Engineer/reviewer skill prompt changes to teach them to WRITE the
  generic external-blocker artifact (currently they write the dated
  version; the glob handles both)
- Whether wiki_collect should run during external-blocker waits (separate
  policy decision; current behavior: supervisor returns at waiting state
  before planner runs, so wiki_collect is not scheduled either)
