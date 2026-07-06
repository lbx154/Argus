---
name: Unit Test Model-Boundary Perf Optimization
description: A playbook for optimizing slow unit tests that accidentally cross model or subprocess boundaries, while preserving deterministic assertions and documenting the measured performance change.
category: test-performance
version: 1
created_at: 2026-07-05T10:53:58.908511+00:00
task_history:
  - "Complete only the `perf_tuning` optimize stage. Read `research/GROUND_TRUTH.md`, `research/perf_isolation/SUBPROCESS_WAIT_TRACE.md`, `tests/tools/test_subagent_supervisor.py`, and `argus_skill/tools/s"
---

# Unit Test Model-Boundary Perf Optimization
## Description
A playbook for optimizing slow unit tests that accidentally cross model or subprocess boundaries, while preserving deterministic assertions and documenting the measured performance change.

## Category
test-performance

## When to use
- A perf optimize stage targets slow unit tests caused by live model, CLI, subprocess, or executable-discovery calls.
- Existing isolation artifacts identify a baseline wait trace or slow boundary such as `<model-command>`, `<subprocess.run>`, or `<summary-helper>`.
- The requested fix should prefer test-only monkeypatches, fakes, or guards over production behavior changes.
- The task requires an audit/report artifact showing which tests intentionally fake external boundaries and which opt out of live execution.

## When NOT to use
- The slowdown is in production code paths and must be fixed for runtime behavior.
- The failing behavior is nondeterministic logic, data corruption, or concurrency correctness rather than external-boundary waiting.
- The task asks for benchmark tuning of application code rather than unit-test isolation.
- No reusable boundary pattern exists and the change is a one-off assertion update.

## How to solve
1. Read the required context first: `<ground-truth-doc>`, `<isolation-artifact>`, `<target-test-file>`, and `<module-under-test>`. Extract the baseline wall time, suspected boundary call, required preserved contracts, and forbidden edits such as `<state-file-to-avoid>`.

2. Inspect the target tests and module boundary functions. Search for call sites around `<report-builder>`, `<model-summary-helper>`, `<model-check-helper>`, `<model-discuss-helper>`, `<executable-finder>`, and `<subprocess-runner>`. Classify each test as:
   - intentionally faked external/model path,
   - deterministic unit test that should opt out of live model/CLI work,
   - integration-like test that is allowed to exercise a fake subprocess or guarded command path.

3. For each slow deterministic unit test, add the smallest test-only boundary patch before invoking the function under test. Prefer existing monkeypatch fixtures or local fakes:
   ```python
   monkeypatch.setattr(<module>, "<model_boundary_helper>", lambda *args, **kwargs: "")
   ```
   Use the return value that preserves the deterministic branch being asserted. For example, return an empty summary when the test is validating fallback report text, status labels, or reply instructions.

4. Preserve existing assertions. Do not weaken checks for required text such as `<expected-concern-label>`, `<expected-status>`, `<expected-reply-instruction>`, or `<expected-command>`. The optimized test should prove the same user-visible contract without waiting on `<model-command>`.

5. If another unintended live boundary is found, patch that test with a fake, guard, or explicit monkeypatch at the test boundary. Do not change production runtime behavior unless measured evidence shows a test-only fix cannot satisfy the stage; if production behavior must change, document the evidence and keep the change narrowly scoped.

6. Write `<audit-output-md>` with:
   - audited boundary functions and subprocess paths,
   - tests that intentionally fake model subprocesses,
   - deterministic tests that opt out of live model/CLI calls,
   - any newly patched unintentional live path,
   - confirmation that production runtime behavior was preserved or the measured reason it was not.

7. Run the targeted before/after command required by the stage, capturing output to `<optimized-durations-output>`:
   ```bash
   ${<PYTHON_ENV_VAR>:-python} -m pytest --durations=50 -q <target-test-node-1> <target-test-node-2> | tee <optimized-durations-output>
   ```
   Confirm it passes, has no real `<model-command>` wait, and meets `<wall-time-threshold>` or `<speedup-threshold>` versus `<baseline-seconds>`.

8. Run any required proof test for intentionally faked model-authored behavior:
   ```bash
   ${<PYTHON_ENV_VAR>:-python} -m pytest -q <intentional-fake-test-node>
   ```

9. Run lint or formatting checks requested by the task:
   ```bash
   <lint-command> <target-test-file>
   ```

10. Write `<optimization-report-md>` with:
   - baseline from existing isolation artifacts,
   - optimized timing and speedup calculation,
   - changed files,
   - commands run and pass/fail results,
   - preserved contracts,
   - confirmation that forbidden files such as `<state-file-to-avoid>` were not edited.

## Pitfalls
- Patching the production model/subprocess path when a test-local monkeypatch would satisfy the perf stage.
- Returning a fake value that bypasses the deterministic branch the test is supposed to verify.
- Weakening assertions to make the optimized test pass faster.
- Forgetting executable-discovery helpers such as `<executable-finder>` can still lead to real CLI execution.
- Recording timings without the exact command output needed for acceptance.
- Editing pipeline or state files that the task explicitly forbids.
- Treating intentionally faked integration coverage as accidental and removing useful boundary tests.