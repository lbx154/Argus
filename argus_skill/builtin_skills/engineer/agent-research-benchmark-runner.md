---
name: Agent Research Benchmark Runner
description: Run agent-research benchmark experiments reproducibly, including baselines, ablations, manifests, logs, cost fields, and resumable background status files.
category: research-experiments
version: 1
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
   - Do not use a fixed small row target as the full-paper stopping condition. A single-family run can be useful, but final evidence must be a multi-family, multi-condition matrix. If cost limits the run, write the exact missing benchmark families/conditions into `paper/EVIDENCE_GAPS.json` and keep the paper blocked or pilot-scoped.
   - Hard refusal: do not treat synthetic tasks, generated tasks, hand-authored proxy graphs, or local pseudo-benchmarks as full EMNLP evidence. If real benchmark sources do not fit the idea, pivot or block; do not synthesize a replacement benchmark.
   - Require the full-paper run matrix to cover the selected benchmark families with raw scored rows for every required condition. A small or single-source synthetic run is a smoke/pilot only and must queue a scale-up or public-benchmark validation run before final EMNLP readiness.
   - Required baselines/conditions are part of the run matrix, not optional metadata. Include the strongest feasible literature/frontier baselines for the selected real benchmarks plus any diagnostic no-skill or lexical baselines. For agent-skill/memory projects this normally means `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, the strongest benchmark-reported baselines that can be run or reproduced, and the proposed method (or a documented domain-specific replacement), each run on the same multi-source benchmark matrix when used for final claims.
   - Benchmark construction is not execution. A populated `benchmarks/full/tasks.jsonl` or manifest is only a candidate split; it does not count as full-run evidence until a completed `experiments/<run_id>/` directory contains raw scored rows for every required condition.
   - Hard prohibition: do not copy, duplicate, relabel, suffix, shuffle, or otherwise rename the same pilot episodes to inflate the benchmark size. Repeated tasks with new IDs such as `_r2`, `_copy`, `_dup`, or equivalent renaming are experiment-integrity failures, not scale-up.
   - Write or update `experiments/BENCHMARK_PROVENANCE.md` and, when possible, `experiments/BENCHMARK_PROVENANCE.json` with `uses_existing_real_benchmark: true`, `benchmark_type: "public"` or `"official_release"`, and a **Selected benchmark sources** table/list. For every selected benchmark/component, record name, URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count contributed, license/access notes, why it is practical/frontier for the claim, surveyed benchmark alternatives, and whether the run is a pilot or full benchmark.
   - If no local GPU is configured, use the approved hosted LLM route for runnable agent evaluations instead of downgrading to an oracle/toy policy; `gpt-5-mini` is the default low-cost no-GPU backbone unless the operator specifies another model. Record model id, endpoint/provider class, temperature, top_p, max_tokens, request/token budget, seed policy, and stopping rules in internal run manifests. In the manuscript, report only paper-facing evaluated-system facts such as model/backend, benchmark, metric, budget/decoding, and high-level cost; do not expose local device/cache/path or Argus/Codex route configuration.
   - Write `experiments/MODEL_SCALE_PLAN.md` or equivalent plan fields before training: model/backbone, parameter count, trainable parameter count, adaptation method, dataset size, internal GPU/memory strategy, expected GPU-hours, and why this is a meaningful frontier-domain model rather than a toy scorer. Local GPU ordinals, CUDA variables, workstation names, and cache paths belong only in logs/manifests, not manuscript prose.

2. Preflight the environment:
   - Record Python version, relevant env vars without secrets, git commit or working-tree summary, and available benchmark scripts in run manifests/logs only. These local execution details are not paper-facing prose.
   - Confirm model/data cache variables point at the project-local store under `./models/` before any dataset/model download: `HF_HOME=$(pwd)/models/huggingface`, `HUGGINGFACE_HUB_CACHE=$(pwd)/models/huggingface/hub`, `HF_DATASETS_CACHE=$(pwd)/models/huggingface/datasets`, `TRANSFORMERS_CACHE=$(pwd)/models/huggingface/hub`, and `TORCH_HOME=$(pwd)/models/torch`. The seeded `code/gpu_env.py` does this for you: call `gpu_env.configure_caches()` at the top of any script before importing transformers, or run `.venv/bin/python code/gpu_env.py` for a readiness check. If a value is missing, export it to the project-local path; each project owns its weights (see the training-infrastructure-guide skill).
   - Check required commands with `--help` or dry-run where available.
   - If running containers or external APIs, verify credentials are present without printing secret values.

3. Create a unique run id:
   - Format: `experiments/<short-topic>-<YYYYMMDDTHHMMSSZ>/`.
   - Write `manifest.json` before launching any run.
   - Include objective, command list, model/backend, budget cap, expected outputs, source snapshot, and parent plan path.
   - Also create `status.json`, `progress.jsonl`, `stdout.log`, `stderr.log`, and document the `STOP` cancellation file before the first expensive call.
   - The seeded `code/experiment_io.py` writes this whole contract for you: wrap your worker in `experiment_io.RunWriter(run_dir, method=..., manifest={...})` and call `run.record(task_id=..., score=...)` per trial. It emits `manifest.json`/`status.json`/`progress.jsonl`/`results.jsonl`, handles `STOP` (writes `run_cancelled`, exits 130), and `experiment_io.validate_run(run_dir)` self-audits row counts. Prefer it over re-implementing run bookkeeping by hand.
   - Update `research/PIPELINE_STATE.json` with the run id and set the run stage to `running`; never mark it `done` until raw result rows and the status/progress artifacts exist.

4. Run in stages — use subagent for GPU tasks:
   - Start with a smoke run that is cheap and fast.
   - Only launch full baselines/ablations after smoke passes.
   - **Before any `scale=full` RL/training launch, clear the RUN CONTRACT gate.**
     The frozen `research/RUN_CONTRACT.json` is the single source of truth for the
     launch knobs (LR, group size / `num_generations`, total steps, batch, model,
     curriculum hash); the launch must match it and cite a feasibility packet
     proving the EXACT curriculum is non-saturating. The `subagent` pre-launch
     interlock REFUSES a drifting or contract-less full-scale RL launch, so pass
     `--run-contract research/RUN_CONTRACT.json --feasibility-packet <packet.json>
     --curriculum-hash <hash>` and dry-check with `python -m
     argus_skill.skills.run_contract check-launch ...`. See the
     `rl-training-collapse-diagnosis` skill for the freeze → probe → launch flow.
   - **CRITICAL: use the subagent system for any GPU training/inference/evaluation >60s:**
     ```bash
     python -m argus_skill.tools.subagent submit \
       --task-id train-grpo-lora \
       --description "Train zImage LoRA with GRPO on GenEval" \
       --command ".venv/bin/python code/train.py --config config.yaml"
     ```
   - To launch a whole method×benchmark matrix at once, define `experiments/MATRIX.json` and run `.venv/bin/python code/run_experiments.py submit` (one non-blocking sub-agent job per condition, with explicit per-condition GPU assignment for parallel multi-GPU use), then poll with `code/run_experiments.py status`.
   - **Many independent targets (a multi-task benchmark) → fan out PER TASK, not just per condition.** The matrix above fans out per *method×condition* (a few coarse conditions, each looping all tasks internally). A benchmark with many *independent* targets — N kernels/tasks each in its own directory (`kernels/<task_id>/…`, one solution file) — has a second, wider axis: **one worker per task/target**. When targets are independent and own **disjoint files** and you have spare parallel capacity, do not grind them one-at-a-time in a single loop — fan them out, two ways:
     - *Per-task subagents* — submit one subagent per task (you stay the single brain; workers run fixed build/score scripts). Lane the task ids (`<lane>::<task>`) so a parked supervisor discussion in one lane never blocks the others.
     - *A teammate-engineer team (DEFAULT for many-target reasoning)* — when each target needs real per-target reasoning (not just a re-run of one script), use the **`Agent Team Lead`** skill + `python -m argus_skill.tools.team` as a **dynamic rolling pool**: launch ONE detached `team coordinate --width N` that keeps N teammate engineers always in flight from a **priority backlog you maintain** (`team form`), each owning its target dir, running its own edit→run→measure→improve loop, self-claimed from the shared board; you (the lead) stay a pure decider — `pool-set` to heartbeat/tune width, read each shard, accept only measured improvements, restock the backlog (breadth/depth). Do NOT run teams as fixed `wait`-ed batches and do NOT use per-task subagents for the cross-target fan-out when targets need reasoning. Two teammates never write the same file (each owns disjoint `owns_paths`).
   - **Bound the fan-out by REAL resources, and decide it yourself.** Concurrent workers ≤ usable accelerators/budget — e.g. only 2 scoring cards allotted ⇒ at most ~2 concurrent jobs, so a team/pool of ~2, not N. Whether to fan out, and how wide, is YOUR judgment from the task shape + free capacity; there is no keyword trigger. Sequential, tightly-coupled, same-file, or capacity-saturated work stays solo (a 2-in-flight subagent pattern already exploits a 2-card budget).
   - **Saturate the GPU on every real run — train fast, do not crawl.** Before launching a training/RL job, size the batch and sequence to fill the card, not to be cheap: scale per-device batch size + gradient accumulation, `max_len`/sequence length, and (for GRPO/PPO/RLVR) `num_generations`/group size up to the largest that fit. A reasoning-RL run whose `clipped_ratio` saturates or whose completions sit at the cap has `max_completion_length` set too **small** — raise it rather than shrinking it. Low GPU util% or low VRAM on an allocated card is a blocker, not a thrifty default; verify once with `nvidia-smi` and record peak VRAM, util%, and step-time/throughput. Going small is allowed only for a smoke run or a documented research/ablation reason. See the training-infrastructure skill's hardware-saturation contract.
   - **For vLLM eval/inference, raise the throughput knobs explicitly** — a conservative default leaves the card at low VRAM/util. Set `gpu_memory_utilization` to ~0.85–0.92 (not ~0.55), `max_num_seqs` to ~64–256 (not ~8) with a matching `max_num_batched_tokens`, size `max_model_len`/`max_tokens` to the real prompt+generation need (so reasoning answers aren't truncated), use `tensor_parallel_size=N` for large models or fan one condition per GPU, keep CUDA graphs (avoid `enforce_eager=True` unless required), and submit all tasks so continuous batching keeps the GPU busy. If a seeded runner (e.g. `code/run_condition.py`) hard-codes low values, raise the defaults or pass larger ones rather than inheriting the trickle.
   - After submitting GPU tasks, **continue other work** (prepare analysis templates, draft paper sections, write code for next condition). Do NOT wait/sleep/block.
   - Check progress periodically: `python -m argus_skill.tools.subagent status --task-id train-grpo-lora`
   - Use the project venv (`.venv/bin/python`) for all ML commands, NOT the argus-skill venv.
   - **Pick instruction-tuned models, not base/pretrained checkpoints, for any method that expects to follow prompts, format answers, or do reasoning RL/eval.** Prefer the `-Instruct`/`-Chat`/`-IT` variant over the same-size base model (e.g. `Qwen3.5-9B-Instruct`, NOT `Qwen3.5-9B-base`). A base model has no instruction-following prior, so its near-chance/format-collapsed outputs look like a dead method when the real cause is the wrong checkpoint. Only use a base model when the experiment is explicitly about base-model behaviour (document why).
   - **Consult the experiment memory before launching or pivoting.** Read the project ledger `research/EXPERIMENT_HISTORY.jsonl` (every past supervised run: concern, stop reason, resolution, headline metric) and, for a specific prior run, its co-located `experiments/<run>/SUPERVISOR_VERDICT.md` + `DISCUSSION.md`. These persist after the supervisor process exits, so a later mission can see WHY a past run succeeded or failed and must not repeat a known dead-end or re-make a fix already agreed.
   - The full run must report unique semantic tasks for each selected benchmark family in the canonical results table. Use documented splits from multiple real/frontier benchmarks; do not relabel, duplicate, generate, or suffix-copy a pilot as full evidence.
   - The full run must also write per-condition raw rows (`results.jsonl`, `progress.jsonl`, or equivalent) with fields such as `method`, `task_id`, and a scored outcome. Do not rely on `status.json task_count` alone: the validator counts distinct raw task ids per method/condition and rejects declared-complete runs with too few rows.
   - The canonical run output must be shaped for a large paper-facing results matrix: every row should carry benchmark/source family, official source/version, task count/split, method/baseline name, evaluated model/backend, metric, budget/decoding/stopping rule, and raw score fields. If these fields are missing, fix the collector before writing the paper.
   - Before marking the run stage done or starting final analysis, self-audit the full-scale experiment-evidence requirement (completed raw scored rows under `experiments/**` for every required method/baseline condition) before claiming readiness; the L2 reviewer verifies these artifacts directly against the run stage checklist. Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
   - For any command that may exceed 60 seconds or 5 model/API calls, run it as a background process and write `pid`, `stdout.log`, `stderr.log`, `status.json`, and `progress.jsonl`.
   - For short commands, capture full stdout/stderr into the run directory.
   - The experiment worker must append a progress JSON line before and after every trial/model call, flush/fsync after every line, and atomically update `status.json`.
   - Print human-readable progress to stdout, e.g. `[run] 17/48 checklist_verify json_schema_004 done pass=1`, so terminal users can see live logs.
   - Check for `STOP` before each expensive call and at least every 30 seconds; on cancellation write `run_cancelled`, set status to `cancelled`, and exit 130.
   - Implement early-stop invariants: stop if repeated validator failures, auth/model errors, unpaired conditions, broken metrics, model mismatch, or budget overrun show the experiment no longer matches the plan.
   - Do not block the agent while a long experiment runs. After launch, verify the PID and first progress events, then continue independent paper/analysis work or answer operator guidance while monitoring the run.
   - Long-tail anti-pattern (throughput leak): never sit in a repeated status-poll loop waiting for a slowly-advancing run to finish (e.g. an eval grinding through the last fraction of tasks). Re-polling the matrix/status burns mission turns and wall-clock while producing zero new evidence. Once you have confirmed a run is healthy and making forward progress, record a one-line resume checkpoint (run-dir + how to collect) and SWITCH to work that does not depend on that run completing. (1) Do not leave GPUs idle: if any GPU is idle or under-filled, launch the next queued/unstarted condition on it (or raise concurrency/`--batch-size`/`--gpu-memory-utilization` on an under-utilized run) so the tail is overlapped, not waited on. (2) Also make non-blocking forward progress the tail does not gate: write and dry-run the NEXT experiment's scripts/configs so they are ready to launch the instant a GPU frees; build the analysis and figure/table-generation scripts against the expected results schema so they run the moment rows land; draft the `RUN_REPORT.md`/`AUDIT_PACKET.md` skeletons; and, within mission scope, advance paper sections and figure drafts. Treat every block of long-tail wait as time to prepare the next stage, never as idle waiting. Only come back to a long-tail run to collect it when its PID exits or `status.json` flips to a terminal state.
   - Completed-run handoff is mandatory: if a background run reaches `completed`, `failed`, `cancelled`, or its PID exits while the mission is still active, collect it in the same mission before waiting, finishing, or relying on the planner. Read `status.json`, tail logs, count raw rows, write/update `RUN_REPORT.md`, self-audit the full-scale experiment-evidence requirement, update `research/PIPELINE_STATE.json`, and either advance to analysis or mark `pivot`/`rejected` when the result invalidates the paper-positive thesis. Do not leave a completed run uncollected with only token-only waiting or watchdog heartbeats.
   - The supervisor STOPS the run on any concern, then DISCUSSES with you in a shared FILE. When a `Subagent Report` arrives with an `EARLY-STOPPED` event, the run is already halted and the supervisor is PARKED on the task's discussion thread waiting for you. The canonical transcript is `discussion.jsonl`, mirrored to a human-readable `experiments/<run>/DISCUSSION.md` co-located with the experiment — read that file to see the full back-and-forth. Nothing resumes until you reply. After you decide your action, you MUST reply with your rationale — why you will act this way and, if you are overriding the supervisor's suggestion, why NOT its alternative. Use: `${ARGUS_SKILL_PYTHON:-python3} -m argus_skill.tools.subagent reply --task-id <id> --message "<rationale>"`. Your reply is appended to the same shared file; the parked supervisor reads it and either agrees on the fix (resolving the concern) or pushes back with a counter-argument — a back-and-forth that all lives in that one file.
   - **The system ENFORCES this loop: while a supervisor is parked on an open discussion, `subagent submit` is BLOCKED and will refuse to launch any new run.** You cannot bypass a concern by silently starting something else — you must engage. Reply, let the discussion reach a resolution, and only then relaunch (with revised idea/hyperparameters — that is YOUR call once it settles). `reply` itself is never blocked. If, and only if, you have a deliberate, documented reason to proceed against an unresolved concern, re-run submit with `--override-discussion "<reason>"` (recorded to the experiment ledger). The run stays stopped throughout; do not silently act against the advice or leave the supervisor parked without a reply.

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
   - Matrix substantially complete, only a background tail left: when every required condition is either collected or still running in the background under a supervised subagent, and the ONLY remaining run-stage work is that near-complete tail finishing (no launchable conditions left), do NOT block the mission polling it. Collect every already-complete condition, record a one-line collect checkpoint for the tail (its supervised subagent reports completion on its own), and proceed NON-BLOCKINGLY into analysis/draft prep (results_table/significance generators against the expected schema, figures, RESULTS_REPORT skeleton, intro/method/related-work). The run-stage gate (full matrix) is still only declared satisfied once the tail completes and is collected — overlapping the next stage's prep does not skip that gate.
   - If the user reports the experiment design is wrong, create the `STOP` file, wait for `run_cancelled` or terminate the recorded PID, and revise the plan before relaunching.

7. Update research tracking:
   - Update `research/CLAIMS_TO_TEST.md` with `running`, `supported`, `weakened`, or `rejected`.
   - Write `experiments/<run_id>/RUN_REPORT.md` with commands, raw artifact paths, and next actions.
   - Update `research/PIPELINE_STATE.json` with `done`, `blocked`, or `pivot` for the run stage. Use `pivot` when the completed run invalidates the paper direction rather than forcing a writing task.
   - **Rule out trivial training-config artifacts before declaring a method dead or pivoting.** A negative RL result whose training rollouts are clipping at the cap (`clipped_ratio` near 1.0, near-zero verifier reward, zero within-group reward variance, `mean_terminated_length`≈0) is a **truncation artifact, not evidence the reward signal is unfixable**. The cheapest, mandatory first experiment is to raise the training generation budget (`max_completion_length`) to at least the evaluation generation length (and confirm raw rollouts actually reach a parseable/boxed answer), then re-test the SAME method — do this BEFORE adopting a new method or writing a pivot plan. Never shrink the cap to save compute when completions are already clipping; that starves the reward and makes the artifact worse. A train/eval generation-length mismatch (training cap below the eval `max_tokens`) must be eliminated before any pivot. Also confirm the checkpoint is an instruction-tuned model, not a base model, before blaming the method. Before writing any pivot plan, review `research/EXPERIMENT_HISTORY.jsonl` and the run's `SUPERVISOR_VERDICT.md`/`DISCUSSION.md` so you do not pivot away from a method whose only flaw was a config artifact already diagnosed.

## Response shape
- Report the run id, launched/completed commands, raw artifact paths, and current status.
- Do not claim success unless the result file or verifier output is present and quoted.
