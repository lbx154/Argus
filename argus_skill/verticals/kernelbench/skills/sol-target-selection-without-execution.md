---
name: SOL Target Selection Without Execution
description: Reusable playbook for selecting the next benchmark optimization target by inventorying local task definitions, excluding already accepted targets, optionally collecting public anchor signals, and producing auditable `NEXT_TARGET_SELECTION.{md,json}` artifacts without editing benchmark/candidate/scorer files or running GPU work.
category: benchmark-optimization-planning
version: 1
created_at: 2026-07-05T22:16:23.315419+00:00
task_history:
  - "Read AGENTS.md, research/GROUND_TRUTH.md, optimize/OFFICIAL_SOL_SUBMISSION_PATH.json, and the SOL optimization/runtime skills. Without editing benchmark/scorer/candidate files or running GPU work, cre"
---

# SOL Target Selection Without Execution
## Description
Reusable playbook for selecting the next benchmark optimization target by inventorying local task definitions, excluding already accepted targets, optionally collecting public anchor signals, and producing auditable `NEXT_TARGET_SELECTION.{md,json}` artifacts without editing benchmark/candidate/scorer files or running GPU work.

## Category
benchmark-optimization-planning

## When to use
- Need to choose the next optimization target for a SOL-style benchmark suite.
- Need a ranked target shortlist based on local runtime, public anchors, workload count, implementation risk, and fit with existing optimization patterns.
- Need reproducible planning artifacts, usually `<output_dir>/NEXT_TARGET_SELECTION.md` and `<output_dir>/NEXT_TARGET_SELECTION.json`.
- The task explicitly forbids submissions, GPU execution, benchmark mutation, or claims of official SOL movement.

## When NOT to use
- The user asks to implement or submit an optimized kernel.
- The user asks to run GPU benchmarks, scorer jobs, or candidate validation.
- The benchmark definitions or accepted-target source are unavailable and cannot be read.
- The task requires official leaderboard/SOL claims instead of unofficial prioritization.

## How to solve
1. Read governing instructions first:
   - Open `<repo>/AGENTS.md`, `<repo>/research/GROUND_TRUTH.md`, `<repo>/<output_dir>/OFFICIAL_SOL_SUBMISSION_PATH.json`, and any local optimization/runtime skill docs matching patterns such as:
     ```bash
     rg -n "SOL|optimization|runtime|submission|candidate|public API" <repo> <skills_dir>
     ```
   - Extract hard constraints, especially prohibited paths, no-GPU requirements, accepted-submission semantics, public API documentation, and required output format.

2. Establish a read-only work boundary:
   - Do not edit files under `<repo>/benchmark`, `<repo>/candidate`, scorer directories, or submitted candidate paths.
   - Do not run commands that compile, benchmark, invoke CUDA/GPU, submit, or mutate official state.
   - Restrict commands to inspection/parsing, for example `rg`, `find`, `jq`, and short CPU-only scripts.

3. Inventory all local benchmark tasks:
   - Discover definitions with:
     ```bash
     find <repo>/SOL-ExecBench/data/benchmark -path "*/definition.json" -type f | sort
     ```
   - Parse each `definition.json` using a structured parser, not ad hoc text slicing.
   - Record at least:
     - `target_id` or stable relative path-derived identifier
     - definition path
     - operation/family name
     - workload/input count
     - available local timing fields such as `<t_sol>`, `<baseline_time>`, `<reference_time>`, or equivalent
     - dtype/shape/parameter hints useful for risk assessment
   - Verify the discovered count against the expected task count from the user or instruction docs. If it differs, report the mismatch in both artifacts.

4. Identify already accepted targets:
   - Parse `<repo>/<output_dir>/OFFICIAL_SOL_SUBMISSION_PATH.json`.
   - Derive accepted target identifiers from explicit fields first; otherwise derive them from submitted paths, filenames, or records documented in `GROUND_TRUTH.md`.
   - Mark every inventory row with `accepted: true|false` and exclude accepted rows from final candidate ranking unless the user explicitly asks for a retrospective report.

5. Collect public anchor records only through documented public APIs:
   - Use the API base URL, endpoint shape, parameters, auth requirements, and rate limits documented in the repo or skill docs.
   - Record every attempted URL, request parameters, HTTP status, and response summary.
   - If the API is inaccessible, unauthenticated, rate-limited, or undocumented, continue without anchors and mark `public_anchor_status` accordingly.
   - Treat anchors as unofficial prioritization signals. Never convert them into official SOL%, and never claim they reflect accepted candidate performance.

6. Normalize comparable metrics:
   - For each unaccepted target, compute fields only when source data supports them:
     - `local_t_sol`
     - `public_anchor_t_sol`
     - `gap_abs = public_anchor_t_sol - local_t_sol`
     - `gap_ratio = public_anchor_t_sol / local_t_sol`
     - `workload_count`
   - Preserve `null` for unknown values rather than fabricating estimates.
   - Include `metric_sources` pointing to the exact local file path or API URL used.

7. Score candidates with transparent heuristics:
   - Use a weighted, explainable ranking such as:
     - known local/public gap: higher is better
     - correctness simplicity: elementwise/reduction/shape-stable kernels lower risk than numerically fragile or highly stateful kernels
     - workload count: more workloads can move aggregate SOL more, but penalize excessive heterogeneity
     - operation family: favor families with clear optimization templates
     - fit with existing fused/DPS/CUDA patterns: favor targets resembling already successful local patterns
     - implementation risk: penalize atomics, dynamic shapes, precision-sensitive reductions, complex indexing, or unsupported dtypes
   - Keep weights in JSON so the ranking is auditable.
   - Add a short rationale string per ranked target.

8. Select one next target:
   - Choose the highest-ranked unaccepted target unless a lower-ranked target has materially better risk-adjusted expected movement.
   - In the rationale, name the decisive factors: known gap, workload count, operation family, implementation simplicity, and compatibility with existing fused/DPS/CUDA patterns.
   - State explicitly that the choice is a planning recommendation, not an official SOL prediction.

9. Write the JSON artifact:
   - Create `<output_dir>/NEXT_TARGET_SELECTION.json` with:
     - `created_at`
     - `inputs_read`
     - `constraints`
     - `commands_run`
     - `api_requests`
     - `inventory_summary`
     - `accepted_targets`
     - `ranking_method`
     - `top_10_ranked_targets`
     - `chosen_next_target`
     - `notes`
   - Include the exact command strings and API URLs used.
   - Include the clear note: public anchors are unofficial prioritization signals and are not official candidate SOL%.

10. Write the Markdown artifact:
   - Create `<output_dir>/NEXT_TARGET_SELECTION.md` as a concise human-readable companion:
     - Scope and constraints
     - Inputs and commands/API calls
     - Inventory and accepted-target summary
     - Ranking method
     - Top 10 table
     - Chosen next target and rationale
     - Limitations and unofficial-anchor disclaimer

11. Validate artifacts without GPU work:
   - Check JSON parses:
     ```bash
     jq . <output_dir>/NEXT_TARGET_SELECTION.json >/dev/null
     ```
   - Confirm forbidden directories were not modified:
     ```bash
     git status --short
     ```
   - Confirm the artifact contains the required disclaimer and top-10 list:
     ```bash
     rg -n "unofficial|not official|top_10|chosen_next_target" <output_dir>/NEXT_TARGET_SELECTION.*
     ```

## Pitfalls
- Do not infer official SOL movement from public anchors; they are only unofficial ranking signals.
- Do not run benchmark/scorer/candidate commands that might compile, launch CUDA, or submit results.
- Do not edit benchmark definitions, scorer code, candidate kernels, or accepted-submission records.
- Do not rank accepted targets as actionable next targets.
- Do not silently ignore inaccessible public APIs; record the attempted URLs/statuses and proceed with local-only scoring.
- Do not hardcode task counts, target IDs, or API endpoints when they can be discovered from local files or docs.
- Do not fabricate missing timing fields; use `null` and explain how the ranking handled unknowns.