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

## Non-negotiable experiment bar
- Final paper experiments must run on existing real benchmark sources or official task/data releases with real ground truth/evaluation. Synthetic/local benchmark generation is permitted only for smoke tests, unit tests, and debugging, and those rows must never support paper-facing headline claims.
- Use the available GPU capacity for a domain-appropriate substantial model when the method involves learning. A tiny custom scorer, bag-of-words model, prompt-only controller, or exact-oracle lookup can be a baseline or smoke run, not the proposed method, unless the operator explicitly lowers the research scope.
- Full runs should compare against frontier baselines from recent papers, not only trivial no-skill or lexical baselines. If a strong baseline cannot be run, record the blocked baseline and choose an adjacent real benchmark/method where a fair comparison is possible.

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
   - Treat benchmark choice as part of literature review, not an implementation afterthought. Survey recent/frontier and widely used benchmark sources before any local harness work; for agent papers, prefer common sources such as ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web-style web tasks, GAIA-style assistant tasks, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, or an ACL Anthology benchmark matching the domain.
   - For a full-paper claim, do not rely on a single benchmark source. Select and execute a diversified benchmark mix: at least 3 independent real/frontier benchmark suites, official task releases, or source families are the hard minimum for final EMNLP evidence. Same-family variants of one suite count as one source. Planned diagnostics do not count until raw scored rows exist. Use the mix to test different failure modes and show that the method works beyond one dataset family.
   - Hard refusal: do not treat synthetic tasks, generated tasks, hand-authored proxy graphs, or local pseudo-benchmarks as full EMNLP evidence. If real benchmark sources do not fit the idea, pivot or block; do not synthesize a replacement benchmark.
   - Require the full-paper run matrix to cover the selected benchmark families with raw scored rows for every required condition. A small or single-source synthetic run is a smoke/pilot only and must queue a scale-up or public-benchmark validation run before final EMNLP readiness.
   - Required baselines/conditions are part of the run matrix, not optional metadata. Include the strongest feasible literature/frontier baselines for the selected real benchmarks plus any diagnostic no-skill or lexical baselines. For agent-skill/memory projects this normally means `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, the strongest benchmark-reported baselines that can be run or reproduced, and the proposed method (or a documented domain-specific replacement), each run on the same multi-source benchmark matrix when used for final claims.
   - Benchmark construction is not execution. A populated `benchmarks/full/tasks.jsonl` or manifest is only a candidate split; it does not count as full-run evidence until a completed `experiments/<run_id>/` directory contains raw scored rows for every required condition.
   - Hard prohibition: do not copy, duplicate, relabel, suffix, shuffle, or otherwise rename the same pilot episodes to inflate the benchmark size. Repeated tasks with new IDs such as `_r2`, `_copy`, `_dup`, or equivalent renaming are experiment-integrity failures, not scale-up.
   - Write or update `experiments/BENCHMARK_PROVENANCE.md` and, when possible, `experiments/BENCHMARK_PROVENANCE.json` with `uses_existing_real_benchmark: true`, `benchmark_type: "public"` or `"official_release"`, and a **Selected benchmark sources** table/list. For every selected benchmark/component, record name, URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count contributed, license/access notes, why it is practical/frontier for the claim, surveyed benchmark alternatives, and whether the run is a pilot or full benchmark.
   - If no local GPU is configured, use the approved hosted LLM route for runnable agent evaluations instead of downgrading to an oracle/toy policy; `gpt-5-mini` is the default low-cost no-GPU backbone unless the operator specifies another model. Record model id, endpoint/provider class, temperature, top_p, max_tokens, request/token budget, cache/retry/timeout policy, and stopping rules in internal run manifests. In the manuscript, report only paper-facing evaluated-system facts such as model/backend, benchmark, metric, budget/decoding, and high-level cost; do not expose local device/cache/path or Argus/Codex route configuration.
   - Write `experiments/MODEL_SCALE_PLAN.md` or equivalent plan fields before training: model/backbone, parameter count, trainable parameter count, adaptation method, dataset size, internal GPU/memory strategy, expected GPU-hours, and why this is a meaningful frontier-domain model rather than a toy scorer. Local GPU ordinals, CUDA variables, workstation names, and cache paths belong only in logs/manifests, not manuscript prose.

2. Preflight the environment:
   - Record Python version, relevant env vars without secrets, git commit or working-tree summary, and available benchmark scripts in run manifests/logs only. These local execution details are not paper-facing prose.
   - Confirm model/data cache variables point to the shared host cache before any dataset/model download: `HF_HOME=/root/.cache/huggingface`, `HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub`, `HF_DATASETS_CACHE=/root/.cache/huggingface/datasets`, `TRANSFORMERS_CACHE=/root/.cache/huggingface/hub`, `TORCH_HOME=/root/.cache/torch`, and `XDG_CACHE_HOME=/root/.cache`. If a value is missing, export it to the shared path; do not create project-local model caches.
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
   - The full run must report unique semantic tasks for each selected benchmark family in the canonical results table. Use documented splits from multiple real/frontier benchmarks; do not relabel, duplicate, generate, or suffix-copy a pilot as full evidence.
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
   - Do not use synthetic or locally generated tasks as main paper evidence. If a synthetic smoke test exists, preserve the generation script and mark every artifact `smoke_only`; exclude it from final results tables, headline metrics, and submission-readiness claims.
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
