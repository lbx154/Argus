---
name: Agent Research Benchmark Runner
description: Run agent-research benchmark experiments reproducibly, including baselines, ablations, manifests, logs, cost fields, and resumable background status files.
category: research-experiments
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Agent Research Benchmark Runner

## Description
Execute the experiment plan for an agent-science paper. This is the argus-skill-native equivalent of ARIS experiment-bridge/run-experiment: it emphasizes reproducible manifests, bounded budget, background execution, and raw artifacts suitable for paper claims.

## When to use
- The objective asks to run benchmarks, pilots, ablations, baseline comparisons, or agent research experiments.
- A paper/layout/review gate reports underfilled body, early References, missing full-scale evidence, missing baseline condition, weak ablation, weak failure analysis, or `next_action: run_more_experiments`.
- `research/EXPERIMENT_PLAN.md` or a similar plan exists.
- The repository has `benchmarks/`, `experiments/`, scripts, or tests that can produce measurable results.
- Long runs need to continue while the daemon moves on to other paper or analysis work.

## When NOT to use
- The task is only to draft a plan without launching anything.
- The task is a one-off code fix where ordinary pytest/ruff verification is enough.
- Required credentials, benchmark datasets, or containers are missing and no local smoke alternative is possible.

## How to solve
1. Load the plan:
   - Read `research/EXPERIMENT_PLAN.md` if present.
   - If absent, reconstruct the minimum run matrix from the operator objective and write a short plan before running.
   - Treat benchmark choice as part of literature review, not an implementation afterthought. Survey recent/frontier and widely used benchmark sources before creating local tasks; for agent papers, prefer common sources such as ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web-style web tasks, GAIA-style assistant tasks, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, or an ACL Anthology benchmark matching the domain.
   - For a full-paper claim, do not rely on a single benchmark source. Select a diversified benchmark mix whenever feasible: target 3+ independent real/frontier benchmark suites or task sources, with 2 independent selected sources as the hard minimum. Use the mix to test different failure modes and show that the method works beyond one dataset or synthetic generator.
   - Refuse to treat synthetic tasks as a full EMNLP benchmark unless the plan explains why public benchmarks are infeasible and cites the surveyed benchmark papers/repos that were considered.
   - Require the full-paper run matrix to reach at least 240 unique semantic scored main tasks/episodes (240/250 scale). A 50/60-task synthetic run is a smoke/pilot only and must queue a scale-up or public-benchmark validation run before final EMNLP readiness.
   - Required baselines/conditions are part of the run matrix, not optional metadata. For agent-skill/memory projects this normally means `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method (or a documented domain-specific replacement), each with >=240 distinct scored main tasks/episodes.
   - Benchmark construction is not execution. A populated `benchmarks/full/tasks.jsonl` or manifest is only a candidate split; it does not count as full-run evidence until a completed `experiments/<run_id>/` directory contains raw scored rows for every required condition.
   - Hard prohibition: do not copy, duplicate, relabel, suffix, shuffle, or otherwise rename the same pilot episodes to inflate the benchmark size. Repeated tasks with new IDs such as `_r2`, `_copy`, `_dup`, or equivalent renaming are experiment-integrity failures, not scale-up.
   - Write or update `experiments/BENCHMARK_PROVENANCE.md` and, when possible, `experiments/BENCHMARK_PROVENANCE.json` with a **Selected benchmark sources** table/list. For every selected benchmark/component, record name, URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count contributed, license/access notes, why it is practical/frontier for the claim, surveyed benchmark alternatives, and whether the run is a pilot or full benchmark.

2. Preflight the environment:
   - Record Python version, relevant env vars without secrets, git commit or working-tree summary, and available benchmark scripts.
   - Check required commands with `--help` or dry-run where available.
   - If running containers or external APIs, verify credentials are present without printing secret values.

3. Create a unique run id:
   - Format: `experiments/<short-topic>-<YYYYMMDDTHHMMSSZ>/`.
   - Write `manifest.json` before launching any run.
   - Include objective, command list, model/backend, budget cap, expected outputs, source snapshot, and parent plan path.
   - Also create `status.json`, `progress.jsonl`, `stdout.log`, `stderr.log`, and document the `STOP` cancellation file before the first expensive call.
   - Update `research/PIPELINE_STATE.json` with the run id and set the run stage to `running`; never mark it `done` until raw result rows and the status/progress artifacts exist.

4. Run in stages:
   - Start with a smoke run that is cheap and fast.
   - Only launch full baselines/ablations after smoke passes.
   - The full run must report `n_tasks >= 240` unique semantic tasks for the main overall split in the canonical results table. Use documented splits from multiple real/frontier benchmarks when feasible, or a hybrid with documented source components; do not relabel, duplicate, or suffix-copy an under-240 pilot as full evidence.
   - The full run must also write per-condition raw rows (`results.jsonl`, `progress.jsonl`, or equivalent) with fields such as `method`, `task_id`, and a scored outcome. Do not rely on `status.json task_count` alone: the validator counts distinct raw task ids per method/condition and rejects declared-complete runs with too few rows.
   - Before marking the run stage done or starting final analysis, run `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .`. Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
   - For any command that may exceed 60 seconds or 5 model/API calls, run it as a background process and write `pid`, `stdout.log`, `stderr.log`, `status.json`, and `progress.jsonl`.
   - For short commands, capture full stdout/stderr into the run directory.
   - The experiment worker must append a progress JSON line before and after every trial/model call, flush/fsync after every line, and atomically update `status.json`.
   - Print human-readable progress to stdout, e.g. `[run] 17/48 checklist_verify json_schema_004 done pass=1`, so terminal users can see live logs.
   - Check for `STOP` before each expensive call and at least every 30 seconds; on cancellation write `run_cancelled`, set status to `cancelled`, and exit 130.
   - Implement early-stop invariants: stop if repeated validator failures, auth/model errors, unpaired conditions, broken metrics, model mismatch, or budget overrun show the experiment no longer matches the plan.
   - Do not block the agent while a long experiment runs. After launch, verify the PID and first progress events, then continue independent paper/analysis work or answer operator guidance while monitoring the run.
   - Completed-run handoff is mandatory: if a background run reaches `completed`, `failed`, `cancelled`, or its PID exits while the mission is still active, collect it in the same mission before waiting, finishing, or relying on the planner. Read `status.json`, tail logs, count raw rows, write/update `RUN_REPORT.md`, run `validate-full-scale-evidence`, update `research/PIPELINE_STATE.json`, and either advance to analysis or mark `pivot`/`rejected` when the result invalidates the paper-positive thesis. Do not leave a completed run uncollected with only token-only waiting or watchdog heartbeats.

5. Preserve raw evidence:
   - Never summarize over missing logs; keep raw command output.
   - Write machine-readable result rows to `results.jsonl` or `summary.tsv`.
   - Include token/cost/latency counters when available.
   - If a run fails, save the failure as data rather than deleting it.
   - For any benchmark downloaded from the web, save the retrieval command, source URL, checksum or commit, and any sampling/filtering script. Do not silently hand-create tasks and present them as a public benchmark.
   - For synthetic or hybrid benchmarks, preserve the generation/sampling script and a uniqueness audit showing that benchmark JSONL/records contain distinct prompts/specs/gold answers, not repeated pilot rows with changed IDs.
   - Write an audit packet such as `experiments/<run_id>/AUDIT_PACKET.md` listing manifest, raw results, validators, logs, expected metrics, and known caveats so the submission assurance gate can cold-read experiment integrity.

6. Resume and collect:
   - Inspect existing `experiments/*/status.json` and `pid` before launching duplicates.
   - If a background run completed, collect outputs and update status in the same mission before doing any unrelated work.
   - If still running, record exact resume instructions and continue with independent analysis or paper work.
   - If the user reports the experiment design is wrong, create the `STOP` file, wait for `run_cancelled` or terminate the recorded PID, and revise the plan before relaunching.

7. Update research tracking:
   - Update `research/CLAIMS_TO_TEST.md` with `running`, `supported`, `weakened`, or `rejected`.
   - Write `experiments/<run_id>/RUN_REPORT.md` with commands, raw artifact paths, and next actions.
   - Update `research/PIPELINE_STATE.json` with `done`, `blocked`, or `pivot` for the run stage. Use `pivot` when the completed run invalidates the paper direction rather than forcing a writing task.

## Response shape
- Report the run id, launched/completed commands, raw artifact paths, and current status.
- Do not claim success unless the result file or verifier output is present and quoted.
