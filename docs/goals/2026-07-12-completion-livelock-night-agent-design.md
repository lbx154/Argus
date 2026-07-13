# Completion Livelock Night Agent Design

## Objective

Remove the completion-livelock paths caused by a long-context model omitting
one control-plane field, while preserving Argus's fail-closed quality gates.
Run the implementation through one interactive Codex `/goal` session in tmux,
retain all experiment evidence locally, and push the verified result directly
to `main`.

## Roles

- Operator: owns the top-level objective and architectural guidance.
- Architect: maintains this goal, reviews the worker's state and diff, resolves
  design questions with the operator, and owns the final commit and push.
- Worker: one Codex CLI session, launched as `codex --yolo` inside tmux. It
  executes Task 01 with TDD and does not push.
- Evidence ledger: `docs/experiment/2026-07-12-completion-livelock/`.

## Minimal Production Changes

1. Final-stage reconciliation:
   `Manager.decide_stage_transition()` must run
   `final_stage_completion_decision()` after both empty and non-empty Manager
   responses. A reviewer-certified final stage must not remain pending because
   the Manager response was empty.

2. Bounded terminal reconciliation:
   before spending a Planner call, a non-paper, non-open-ended continuous
   project whose active vertical has reached its own terminal stage with
   `status=done` is deterministically complete. Reuse
   `vertical_reached_own_terminal_stage()`; do not create a second terminal
   parser. Full-paper projects continue to require their existing reviewer
   certification marker.

3. Zero-enqueue backoff:
   when every Planner task is removed by duplicate/failure-family filters,
   return a no-work retry sentinel after entering idle backoff. Do not report
   that work was added and do not immediately call the Planner again.

4. Completed final-submission dedup:
   a completed `scope:final_submission` backlog item must participate in the
   same signature dedup as every other completed Planner item. If the
   uncertified full-paper guard regenerates that identical task, filtering must
   remove it so the zero-enqueue backoff path returns `PLAN_RETRY` instead of
   adding another final task. Remove only the completed-final-submission dedup
   exemption; do not weaken the certification gate.

No prompt expansion, schema relaxation, completion-quality downgrade, broad
refactor, or new state file is allowed.

## Control Flow

```text
operator objective
  -> architect goal + Task 01
  -> tmux Codex /goal
  -> failing regression tests
  -> minimal implementation
  -> targeted tests + broader supervisor tests
  -> experiment ledger update
  -> architect diff review
  -> architect final verification
  -> commit and push main
```

## Monitoring

The tmux session is named `argus-livelock-night-20260712`. The architect checks:

- `tmux capture-pane` for the current Codex checkpoint or question;
- `git status`, `git diff --stat`, and `git diff`;
- the active test process and fresh exit status;
- `docs/goals/2026-07-12-completion-livelock-night-agent-status.md`.

While the goal is active, the architect records a compact checkpoint at least
hourly: current phase, verified evidence, remaining work, and blockers. A
design conflict, scope expansion, or ambiguous completion invariant pauses the
worker and is escalated to the operator.

## Completion Criteria

- All four regressions have tests that failed before their production fixes.
- The final-stage empty-response reproduction completes the final stage.
- A bounded non-paper terminal stage bypasses the Planner and returns project
  done; full-paper behavior remains certification-gated.
- A fully filtered Planner batch enters backoff and does not report work added.
- An uncertified completed final-submission item is not re-enqueued; the
  identical regenerated task is deduplicated and enters backoff.
- Targeted and relevant supervisor suites pass.
- Experiment evidence records commands, outputs, base/final Git checkpoints,
  and states that no ML model checkpoint was used.
- The verified commit is present on `origin/main`.
