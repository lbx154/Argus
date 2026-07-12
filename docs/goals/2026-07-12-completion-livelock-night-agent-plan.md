# Completion Livelock Night Agent Implementation Plan

> **For the tmux Codex worker:** Execute this plan task-by-task with TDD. Do not
> push. Keep changes narrowly limited to the files named below and update the
> experiment ledger before reporting completion.

**Goal:** Make Argus completion control deterministic at terminal boundaries so
an omitted LLM field cannot cause repeated work or a stuck final stage.

**Architecture:** Reuse the existing reviewer certification and
`vertical_reached_own_terminal_stage()` ground truth. Adjust only the Manager's
terminal reconciliation and the Supervisor planning-cycle boundaries.

**Tech Stack:** Python 3.11, pytest, Git, Codex CLI `/goal`, tmux.

---

### Task 1: Final-Stage Empty Response

**Files:**
- Modify: `tests/manager/test_stage_decider.py`
- Modify: `tests/life/test_manager_stage_hook.py`
- Modify: `argus_skill/manager/_core.py`

- [ ] Add a failing unit test where the active stage is `submission`, the
      reviewer returns `done` with a satisfied evidence-bearing checklist and
      `forward_progress=true`, and all three Manager calls return empty output.
      Assert `StageTransition.action == "complete"` and
      `stages.submission.status == "done"`.
- [ ] Run:

```bash
python -m pytest -o addopts='' -q \
  tests/manager/test_stage_decider.py \
  tests/life/test_manager_stage_hook.py
```

Expected before the fix: the new test fails with action `hold` and diagnostic
`empty_output_no_next_stage`.

- [ ] Move the existing `final_stage_completion_decision(...)` call after the
      empty/non-empty response branch:

```python
if not str(raw or "").strip():
    decision = fallback_empty_stage_decision(...)
else:
    ...
    decision = parse_stage_decision(...)

final_decision = final_stage_completion_decision(
    review,
    current_stage=cur,
    stage_order=order,
    trigger_diagnostic=decision.diagnostic,
    trigger_reason=decision.reason,
)
if final_decision is not None:
    decision = final_decision
```

- [ ] Re-run the two test files and confirm they pass.

### Task 2: Derive Bounded Project Completion

**Files:**
- Modify: `tests/test_reviewer_completion_contract.py`
- Modify: `argus_skill/life/supervisor/_planning_cycle.py`

- [ ] Add a failing regression test for a continuous, `open_ended=False`,
      non-paper `speedrun` project with
      `current_stage=report` and `stages.report.status=done`. Use a Planner
      runner that raises if called. Assert `_plan_next_work() is False`.
- [ ] Add or preserve a companion assertion that a `research` project under the
      full-paper gate is not considered complete from terminal-stage status
      alone.
- [ ] Run:

```bash
python -m pytest -o addopts='' -q \
  tests/test_reviewer_completion_contract.py
```

Expected before the fix: the non-paper regression reaches the exploding
Planner runner.

- [ ] After `_resolve_vertical_once()` and before constructing the Planner,
      resolve the active vertical and call
      `vertical_reached_own_terminal_stage()`. Return project done only when:

```python
not self.config.open_ended
and not self._effective_full_paper_gate(self._artifact_root())
and vertical_reached_own_terminal_stage(self._artifact_root(), vertical)
```

Emit a zero-token `life.planner.verdict` and a clear status line so the derived
transition remains observable.

- [ ] Re-run the completion-contract tests.

### Task 3: Back Off When Filtering Enqueues Nothing

**Files:**
- Modify: `tests/life/test_planner_subagent_family_circuit_breaker.py`
- Modify: `argus_skill/life/supervisor/_planning_cycle.py`

- [ ] Change the existing fully-filtered stuck-family regression to expect
      `PLAN_RETRY`, an empty backlog, and a positive suggested backoff.
- [ ] Add an assertion that the Planner verdict reports zero enqueued tasks.
- [ ] Run:

```bash
python -m pytest -o addopts='' -q \
  tests/life/test_planner_subagent_family_circuit_breaker.py
```

Expected before the fix: `_plan_next_work()` returns `True` and resets backoff.

- [ ] After emitting the Planner verdict and handling daemon restart, add:

```python
if not added_titles:
    self._enter_idle_backoff()
    self._emit_status(
        "planner: all proposed tasks were filtered; retrying after backoff"
    )
    return PLAN_RETRY
```

Keep the existing reset-and-`True` path only for real new work.

### Task 4: Verification And Evidence

**Files:**
- Modify: `docs/experiment/2026-07-12-completion-livelock/README.md`
- Modify: `docs/goals/2026-07-12-completion-livelock-night-agent-status.md`

- [ ] Run the focused suites:

```bash
python -m pytest -o addopts='' -q \
  tests/manager/test_stage_decider.py \
  tests/life/test_manager_stage_hook.py \
  tests/test_reviewer_completion_contract.py \
  tests/life/test_planner_subagent_family_circuit_breaker.py \
  tests/skills/test_stage_checklists.py \
  tests/skills/test_verticals.py
```

- [ ] Run the broader supervisor/planner suites:

```bash
python -m pytest -o addopts='' -q \
  tests/life/test_supervisor.py \
  tests/life/test_planner_dag_enqueue.py \
  tests/life/test_state_machine_guards.py \
  tests/planner/test_planner.py
```

- [ ] Run `git diff --check`.
- [ ] Record exact test counts, elapsed times, base SHA, final working-tree
      status, and changed files in the experiment ledger.
- [ ] Report completion to the architect. Do not commit or push.

