---
name: Performance Profile Ground Truth
description: A playbook for completing a profiling-only stage by documenting current behavior, runnable environment facts, measured timings, profiler output, and the single strongest measured bottleneck before any optimization work begins.
category: performance-profiling
version: 1
created_at: 2026-07-05T09:52:47.834598+00:00
task_history:
  - "Complete the CURRENT `profile` stage only; do not optimize code yet and do not edit `research/PIPELINE_STATE.json`. Read `AGENTS.md`, `pyproject.toml`, `research/PIPELINE_STATE.json`, and `research/DO"
---

# Performance Profile Ground Truth
## Description
A playbook for completing a profiling-only stage by documenting current behavior, runnable environment facts, measured timings, profiler output, and the single strongest measured bottleneck before any optimization work begins.

## Category
performance-profiling

## When to use
- When a task asks for a `profile`, `measure`, `baseline`, or `ground truth` stage before optimization.
- When the Engineer must create profiling artifacts and a written evidence file.
- When stage metadata may disagree with a Manager gate or task instruction and the mismatch must be recorded rather than corrected.
- When production code must remain unchanged while tests and profilers are run.

## When NOT to use
- When the task asks to implement, tune, or refactor code immediately.
- When profiling data already exists and the task is only to analyze existing artifacts.
- When the required workload is not test-driven or cannot be exercised from local commands.
- When the user explicitly asks to edit pipeline state or stage metadata.

## How to solve
1. Read the required operating context first:
   - `<repo_guidance_file>` such as `AGENTS.md`
   - `<project_config>` such as `pyproject.toml`
   - `<pipeline_state_file>` such as `research/PIPELINE_STATE.json`
   - `<domain_or_task_file>` such as `research/DOMAINS/<domain>.json`
   Record constraints, intended goal, test commands, and any stage-state mismatch. Do not edit the pipeline state file.

2. Create a profiling artifact directory:
   ```bash
   mkdir -p <profile_dir>
   ```
   Use a task-local path such as `research/perf_profile/` when requested.

3. Capture environment facts with commands that actually run in the workspace:
   ```bash
   pwd
   /usr/bin/python --version
   /usr/bin/python -m pytest --version
   /usr/bin/python -m pip show <relevant_package>
   ```
   If package metadata is unavailable, record the failed command and continue.

4. Collect tests before timing:
   ```bash
   /usr/bin/python -m pytest --collect-only -q
   ```
   Save or summarize the collection count. If collection fails, stop profiling and write the failure into `<ground_truth_file>` with the exact command and error summary.

5. Measure baseline timings without profiling first:
   ```bash
   /usr/bin/python -m pytest --durations=50 -q
   ```
   If the full suite is practical, use this as the timing baseline. If it is too slow or times out, record the measured limitation, timeout threshold, and partial output, then select the slowest collected subset or a representative workload:
   ```bash
   /usr/bin/python -m pytest --durations=50 -q <selected_slow_workload>
   ```

6. Run `cProfile` against the selected workload:
   ```bash
   /usr/bin/python -m cProfile -o <profile_dir>/<profile_name>.prof -m pytest -q <selected_slow_workload>
   ```
   Prefer the full slow workload that finished during baseline timing. If the full suite is impractical, profile the slowest practical subset and state why.

7. Export human-readable profiler output:
   ```bash
   /usr/bin/python - <<'PY' > <profile_dir>/<profile_name>_pstats.txt
   import pstats

   profile_path = "<profile_dir>/<profile_name>.prof"
   stats = pstats.Stats(profile_path)

   print("=== sort: cumulative ===")
   stats.strip_dirs().sort_stats("cumulative").print_stats(80)

   print("\n=== sort: total time ===")
   stats.strip_dirs().sort_stats("tottime").print_stats(80)
   PY
   ```

8. Identify one measured top bottleneck candidate from the `pstats` export. Record:
   - file path
   - function name
   - call count
   - total time
   - cumulative time
   - whether it appears under cumulative time, total time, or both

9. Verify the binding constraint that should drive later optimization. Base it on evidence, not speculation. Examples:
   - CPU-bound function dominates cumulative runtime.
   - Excessive call count dominates total time.
   - Test setup or import time dominates the workload.
   - I/O or subprocess behavior is the limiting factor.
   Include the command output or profiler numbers that prove the constraint.

10. Write `<ground_truth_file>` as the first deliverable. It should include:
   - Original goal from the task/domain file.
   - Observed stage-state mismatch, if any, such as `<state_file> says <state_stage>, Manager gate says <gate_stage>`.
   - Runnable test/profiling environment facts.
   - Exact commands run, in order.
   - Measured current-state timings.
   - Profiling artifact paths.
   - Slowest measured bottleneck function with numbers.
   - Verified binding constraint for later optimization.
   - Explicit statement that no production optimization changes were made.

11. Run acceptance checks:
   ```bash
   test -s <ground_truth_file>
   test -s <profile_dir>/<profile_name>.prof
   test -s <profile_dir>/<profile_name>_pstats.txt
   git diff --name-only
   ```
   Confirm the diff contains only allowed research/profiling deliverables and no production optimization changes.

## Pitfalls
- Do not “fix” a stage mismatch by editing the pipeline state; document the mismatch instead.
- Do not optimize, refactor, or clean up production code during the profile stage.
- Do not claim a bottleneck from intuition; use measured `pstats` call count, total time, and cumulative time.
- Do not profile an unmeasured arbitrary test if the suite can first identify slower workloads with `--durations`.
- Do not hide failed or impractical commands; record the exact command, limitation, and fallback workload.
- Do not leave only raw `.prof` output; always include a human-readable `pstats` export.