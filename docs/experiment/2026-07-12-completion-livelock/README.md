# Completion-Livelock Experiment Record

## Purpose

Verify that three control-plane omissions no longer cause a stuck stage or a
repeated planning loop.

## Repository Checkpoints

- Remote: `lbx154/argus-skill`
- Branch: `main`
- Base Git checkpoint:
  `21b3b241ef3696f9593d2f9e88d78e66366358ef`
- Final Git checkpoint: not created yet
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

## Commands And Results

The worker must append the RED and GREEN command outputs here, including exact
test counts and elapsed times. Raw experiment code remains in the repository
tests named by the implementation plan.

