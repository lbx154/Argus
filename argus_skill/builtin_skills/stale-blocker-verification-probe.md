---
name: Stale Blocker Verification Probe
description: A playbook for verifying whether a previously recorded blocker is still real by performing the cheapest decisive firsthand probe, reporting concrete evidence, and taking the smallest next step when the blocker has cleared.
category: verification-probe
version: 1
created_at: 2026-07-05T12:29:57.209075+00:00
task_history:
  - "Verification-probe mission, dispatched by the harness after the planner idled 7 consecutive cycles concluding it was blocked. Do NOT trust the journal's record of the blocker as still current. The rec"
---

# Stale Blocker Verification Probe
## Description
A playbook for verifying whether a previously recorded blocker is still real by performing the cheapest decisive firsthand probe, reporting concrete evidence, and taking the smallest next step when the blocker has cleared.

## Category
verification-probe

## When to use
- A planner, harness, or journal says work is blocked, but the record may be stale.
- The task asks for fresh firsthand evidence of the current state.
- The required outcome is to decide `STILL BLOCKED` versus `CLEARED`.
- The blocker involves an artifact, command, gate, metric, dependency, permission, or external state that can be tested directly.

## When NOT to use
- Do not use for broad debugging or root-cause analysis unless the decisive probe fails.
- Do not use when the user explicitly asks to implement the full feature instead of verifying a blocker.
- Do not rely on journals, summaries, prior logs, or planner conclusions as final evidence.
- Do not run expensive or destructive actions when a cheaper read-only probe can decide the state.

## How to solve
1. Identify the recorded blocker and translate it into a testable condition: `<artifact> exists`, `<command> succeeds`, `<metric> reaches threshold`, `<service> responds`, or `<gate> passes`.

2. Determine the cheapest decisive probe. Prefer read-only checks first:
   - `test -e <path> && stat <path>`
   - `ls -l <path>`
   - `rg <expected_text> <path>`
   - `jq <query> <json_path>`
   - `<command> --dry-run`
   - `<test_command> --filter <single_case>`

3. Actually run the probe now. Capture the command, exit code when available, and enough output to prove the current state.

4. If the probe is inconclusive, run exactly one next cheapest probe that narrows the same condition. Avoid turning the mission into open-ended investigation.

5. Decide plainly:
   - `STILL BLOCKED` if the blocked action still fails or the required artifact/metric/state is absent.
   - `CLEARED` if the blocked action succeeds or the required artifact/metric/state is now present.

6. If still blocked, report:
   - The blocker in present tense.
   - The exact command or check performed.
   - The concrete evidence, such as error text, missing file, failing status, or observed metric.
   - The smallest likely owner/action needed next, if obvious.

7. If cleared, immediately perform the smallest concrete next step that was previously prevented:
   - rerun `<gate_command>`
   - update `<status_artifact>`
   - execute `<next_single_step>`
   - unblock `<dependent_task>`
   Keep it narrow; do not expand scope.

8. Report the final evidence packet:
   - `Status: STILL BLOCKED` or `Status: CLEARED`
   - `Probe: <command>`
   - `Evidence: <key output/file/timestamp/metric>`
   - `Next step taken: <small action>` or `Next step blocked: <reason>`

## Pitfalls
- Treating the journal’s blocker as evidence instead of a hypothesis.
- Reporting “blocked” without running a fresh command.
- Running a broad build or full test suite when `stat`, `rg`, `jq`, or a single targeted command would decide the issue.
- Claiming cleared without proving the blocked condition changed.
- Continuing into unrelated cleanup after the blocker clears.
- Omitting concrete evidence such as command output, file existence, timestamp, exit status, or metric value.