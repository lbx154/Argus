# Completion-Livelock Experiment Record

## Purpose

Verify that four control-plane omissions no longer cause a stuck stage or a
repeated planning loop.

## Repository Checkpoints

- Remote: `lbx154/argus-skill`
- Branch: `main`
- Base Git checkpoint:
  `21b3b241ef3696f9593d2f9e88d78e66366358ef`
- Worker starting HEAD:
  `21198f8c72a5ddaf82cf2d210f539405426c3c89`
- Verified implementation Git checkpoint:
  `1ee127e5d4bb5215f39aa9f66500603d665cb815`
- ML model checkpoint: none; this is a deterministic Python control-flow test
- Codex session checkpoint: tmux session
  `argus-livelock-night-20260712`

## Experiment A: Empty Manager At Final Stage

- Input state: research vertical, `current_stage=submission`,
  `stages.submission.status=pending`.
- Reviewer evidence: `status=done`, non-empty satisfied checklist with evidence,
  `planner_report.forward_progress=true`.
- Perturbation: all three Manager stage-decision responses are empty.
- Baseline result: `hold`, diagnostic `empty_output_no_next_stage`, final stage
  remains pending.
- Expected repaired result: `complete`, final stage status `done`.

## Experiment B: Bounded Terminal Reconciliation

- Input state: non-paper speedrun vertical, `current_stage=report`,
  `stages.report.status=done`, `open_ended=false`.
- Perturbation: Planner runner raises if invoked.
- Baseline result: Planner is invoked because `project_done` is not derived.
- Expected repaired result: `_plan_next_work()` returns `False` without a
  Planner call.
- Safety control: research/full-paper projects still require the existing
  final-submission certification marker.

## Experiment C: Fully Filtered Planner Batch

- Input: Planner proposes only a task belonging to a circuit-broken subagent
  family.
- Baseline result: zero tasks are enqueued but `_plan_next_work()` returns
  `True`, immediately allowing another planning cycle.
- Expected repaired result: zero tasks are enqueued, idle backoff increases,
  and `_plan_next_work()` returns `PLAN_RETRY`.

## Experiment D: Completed Final-Submission Retry

- Input state: full-paper gate active without a certification event; the
  canonical `scope:final_submission` backlog item already exists with
  `status=done`.
- Perturbation: Planner returns `project_done=true`, causing the full-paper
  guard to regenerate the identical final-submission task.
- Baseline result: the completed final item is exempted from signature dedup, a
  second final task is enqueued, backoff is reset, and `_plan_next_work()`
  returns `True`.
- Expected repaired result: the completed item participates in dedup, no second
  task is added, idle backoff increases, and `_plan_next_work()` returns
  `PLAN_RETRY`.

## Commands And Results

### Baseline

```bash
python -m pytest -o addopts='' -q \
  tests/manager/test_stage_decider.py \
  tests/life/test_manager_stage_hook.py \
  tests/test_reviewer_completion_contract.py \
  tests/life/test_planner_subagent_family_circuit_breaker.py
```

Result before adding regressions:

```text
90 passed in 1.78s
```

### Experiment A RED

```bash
python -m pytest -o addopts='' -q \
  tests/manager/test_stage_decider.py \
  tests/life/test_manager_stage_hook.py
```

Result after adding the direct Manager and runtime-hook regressions, before the
production fix:

```text
2 failed, 52 passed in 0.89s
```

Both regressions observed `action == "hold"` instead of `"complete"` after
three empty Manager responses. The final-stage empty fallback remained
`empty_output_no_next_stage`.

### Experiment A GREEN

The same command after moving final-stage reconciliation after the
empty/non-empty branch:

```text
54 passed in 0.77s
```

### Experiment B RED

```bash
python -m pytest -o addopts='' -q \
  tests/test_reviewer_completion_contract.py
```

Result after adding the bounded speedrun terminal-state regression, before the
production fix:

```text
1 failed, 32 passed in 0.82s
```

The exploding Planner runner was reached and `_plan_next_work()` returned
`planner_error` instead of `False`.

### Experiment B GREEN

The same command after adding bounded terminal reconciliation:

```text
33 passed in 0.67s
```

The repaired regression also verifies a zero-token `life.planner.verdict`, no
Planner call, and a project-done status event. Its research/full-paper safety
control still invokes planning and enqueues the existing
`scope:final_submission` certification task when no certification marker
exists.

### Experiment C RED

```bash
python -m pytest -o addopts='' -q \
  tests/life/test_planner_subagent_family_circuit_breaker.py
```

Result after changing the fully filtered regression, before the production
fix:

```text
1 failed, 6 passed in 0.35s
```

The all-filtered batch returned `True` instead of `PLAN_RETRY`.

After the production fix, the same suite exposed a second stale expectation for
the underscore/hyphen fully filtered variant:

```text
1 failed, 6 passed in 0.34s
```

After aligning that existing variant with the same approved invariant:

```text
7 passed in 0.30s
```

The regression verifies an empty backlog, `enqueued_tasks == 0`, and a positive
suggested backoff.

### Focused Verification

```bash
python -m pytest -o addopts='' -q \
  tests/manager/test_stage_decider.py \
  tests/life/test_manager_stage_hook.py \
  tests/test_reviewer_completion_contract.py \
  tests/life/test_planner_subagent_family_circuit_breaker.py \
  tests/skills/test_stage_checklists.py \
  tests/skills/test_verticals.py
```

```text
140 passed in 1.72s
```

### Broader Supervisor And Planner Verification

```bash
python -m pytest -o addopts='' -q \
  tests/life/test_supervisor.py \
  tests/life/test_planner_dag_enqueue.py \
  tests/life/test_state_machine_guards.py \
  tests/planner/test_planner.py
```

```text
63 passed in 0.56s
```

### Diff And Working Tree

```bash
git diff --check
```

Result: exit code `0`, no output.

Working tree before the final ledger edits:

```text
## main...origin/main [ahead 1]
 M argus_skill/life/supervisor/_planning_cycle.py
 M argus_skill/manager/_core.py
 M tests/life/test_manager_stage_hook.py
 M tests/life/test_planner_subagent_family_circuit_breaker.py
 M tests/manager/test_stage_decider.py
 M tests/test_reviewer_completion_contract.py
```

The architect-facing final status, including both ledger files, is recorded in
`docs/goals/2026-07-12-completion-livelock-night-agent-status.md`.

### Final Post-Ledger Verification

The plan's focused and broader commands were rerun after both ledger files were
updated:

```text
focused: 140 passed in 1.63s
broader: 63 passed in 0.49s
git diff --check: exit code 0, no output
```

Final worker working tree:

```text
## main...origin/main [ahead 1]
 M argus_skill/life/supervisor/_planning_cycle.py
 M argus_skill/manager/_core.py
 M docs/experiment/2026-07-12-completion-livelock/README.md
 M docs/goals/2026-07-12-completion-livelock-night-agent-status.md
 M tests/life/test_manager_stage_hook.py
 M tests/life/test_planner_subagent_family_circuit_breaker.py
 M tests/manager/test_stage_decider.py
 M tests/test_reviewer_completion_contract.py
```

### Architect Review Reopen: Experiment D

Completion-contract baseline before adding the architect regression:

```text
33 passed in 0.67s
```

Command:

```bash
python -m pytest -o addopts='' -q \
  tests/test_reviewer_completion_contract.py
```

RED after adding the completed-final-submission reproduction:

```text
1 failed, 33 passed in 0.89s
```

The second uncertified full-paper gate cycle returned `True` instead of
`PLAN_RETRY`, proving that the identical final task was re-enqueued.

GREEN after removing only the completed-final-submission dedup exemption:

```text
34 passed in 0.73s
```

Pre-ledger verification after the reopened fix:

```text
focused: 141 passed in 2.16s
broader: 63 passed in 0.50s
ruff: All checks passed!
git diff --check: exit code 0, no output
```

Final post-amendment verification:

```text
focused: 141 passed in 1.69s
broader: 63 passed in 0.48s
ruff: All checks passed!
git diff --check: exit code 0, no output
```

Final reopened worker tree:

```text
## main...origin/main [ahead 1]
 M argus_skill/life/supervisor/_planning_cycle.py
 M argus_skill/manager/_core.py
 M docs/experiment/2026-07-12-completion-livelock/README.md
 M docs/goals/2026-07-12-completion-livelock-night-agent-design.md
 M docs/goals/2026-07-12-completion-livelock-night-agent-plan.md
 M docs/goals/2026-07-12-completion-livelock-night-agent-status.md
 M tests/life/test_manager_stage_hook.py
 M tests/life/test_planner_subagent_family_circuit_breaker.py
 M tests/manager/test_stage_decider.py
 M tests/test_reviewer_completion_contract.py
```

### Architect Full-Suite Compatibility Correction

Architect-provided full-suite triage before this compatibility correction:

```text
3068 passed, 13 failed
```

One of the thirteen failures was attributable to Task 01:

```text
tests/life/test_lifecycle_supervisor_integration.py::
test_planner_waiting_records_external_dependency_status
```

Its legacy `SimpleNamespace` config intentionally omits `open_ended`. The new
terminal reconcile read `self.config.open_ended` directly and raised
`AttributeError` before the planner waiting verdict could be recorded.

Exact RED command:

```bash
python -m pytest -o addopts='' -q \
  tests/life/test_lifecycle_supervisor_integration.py::\
test_planner_waiting_records_external_dependency_status
```

```text
1 failed in 0.16s
```

The production correction changed only the terminal condition from:

```python
not self.config.open_ended
```

to:

```python
not getattr(self.config, "open_ended", False)
```

Exact GREEN result from the same command:

```text
1 passed in 0.11s
```

Post-correction scoped verification:

```text
focused: 141 passed in 1.74s
broader: 63 passed in 0.49s
ruff: All checks passed!
git diff --check: exit code 0, no output
```

Final worker audit after the ledger append:

```text
exact compatibility test: 1 passed in 0.11s
focused: 141 passed in 1.68s
broader: 63 passed in 0.48s
ruff: All checks passed!
git diff --check: exit code 0, no output
```

### Architect Environment, Baseline, And Release Triage

The initial full run was:

```text
3068 passed, 13 failed, 3 skipped
```

The thirteen failures were classified with a detached worktree at the exact
unmodified base commit:

- One Task 01 compatibility regression (`config.open_ended`) was fixed with
  the `getattr(..., False)` fallback above.
- Five subprocess tests failed because this fresh clone was not installed, so
  subprocesses launched from temporary working directories could not import
  `argus_skill`. The same failures reproduced on unmodified `origin/main`.
  `python -m pip install -e .` fixed the test environment.
- Five release/protocol/runtime-identity tests passed on unmodified
  `origin/main` but failed after the source changes because the generated source
  digest was stale. They were repaired with:

```bash
python scripts/generate_release_manifest.py
python scripts/generate_release_manifest.py --check
```

- Two failures reproduced on unmodified `origin/main` and remain outside this
  patch:
  - `test_missing_output_schema_is_blocked_without_spawning_codex`: this host's
    `/nonexistent` path raises `PermissionError` rather than behaving as a
    missing path.
  - `test_planner_role_gives_stage_authority_to_manager`: the existing built-in
    planner-role text lacks the asserted historical sentence.

After the compatibility fix, editable installation, and release-manifest
refresh, the final full run was:

```text
3079 passed, 2 failed, 3 skipped in 55.47s
```

Both remaining failures are the two unmodified-base failures listed above.
