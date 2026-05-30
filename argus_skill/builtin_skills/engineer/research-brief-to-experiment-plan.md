---
name: Research Brief To Experiment Plan
description: Convert an operator research seed into a literature/code-derived idea and falsifiable experiment plan with hypotheses, baselines, metrics, budgets, and artifact contracts.
category: research-planning
version: 1
scientist_model: gpt-5.5
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Research Brief To Experiment Plan

## Description
Turn a loose operator research direction into a concrete, evidence-first experiment plan. The final idea must come from surveyed papers, trend signals, benchmark gaps, and reusable code sources--not from agent brainstorming. Adapted from ARIS-style research pipeline concepts, but written for argus-skill's single-mission engineer/reviewer loop instead of slash-command skill chaining.

## Non-negotiable research bar
- The selected project must be a frontier-domain project, not a toy mechanism study. Before locking the idea, identify current strong papers, current benchmark leaderboards or reported SOTA baselines, and the concrete gap that remains open.
- **Research taste**: the idea must contain a genuine insight or surprising angle. Ask yourself:
  - "What would make an EMNLP reviewer say 'that's interesting, I hadn't thought of it that way'?"
  - "What is the ONE key insight that makes this work, and why hasn't anyone done it before?"
  - If you can't answer these, the idea needs more thinking, not more engineering.
  - A paper that says "we applied technique A to domain B and it worked" is NOT research — there must be a WHY.
- Default to a training-based or hybrid method when local GPUs can support it. Small bag-of-words scorers, linear heads over hashed tokens, prompt-only wrappers, exact-oracle search policies, or other lightweight proxies are allowed only as smoke tests or baselines; they cannot be the proposed paper system unless the operator explicitly downgrades the scope.
- The proposed method must train or adapt a domain-appropriate modern backbone at meaningful scale for the target field, using LoRA/QLoRA/FSDP/DeepSpeed/Accelerate or an equivalent efficient recipe when full fine-tuning is too expensive. Record the model family, parameter scale, trainable parameters, dataset size, GPU memory plan, and expected GPU-hours.
- Final benchmark evidence must come from existing real benchmarks or their official task/data releases. Do not create a synthetic benchmark, synthetic proxy, generated task set, or locally invented oracle as the main evidence source. Synthetic data may be used only for unit tests, debugging, or clearly labeled smoke tests with no paper-facing result claims.

## Training & inference infrastructure contract (REQUIRED if any training or large-scale inference)

Custom training loops and bare `model.generate()` inference loops are **hard
blockers** at the planner / reviewer gate. Every project that involves
gradient-based training or large-scale inference must lock in an existing
open-source framework on each axis before drafting the experiment plan.

1. **Read `argus_builtin_skills/training-infrastructure-guide.md` first.**
   It is the bundled curated baseline (LLM SFT/DPO/RLHF, agent RL,
   diffusion, LLM inference, API inference). Anchor your selection there.
2. **Then do your own search.** Look at recent arXiv (2026+) repos that
   match your specific domain (e.g. for diffusion RL: SimpleTuner /
   diffusers / SimpleTuner-RL / flow_grpo / Dense_Reward_T2I-style
   official repos; for agent RL: AgentGym-RL / veRL / SLIME; for LLM
   post-training: LLaMA-Factory / TRL / OpenRLHF / veRL). Add at least
   one candidate the bundled guide does not name.
3. **Maintenance bar: 2026-or-later.** Every candidate must have a
   release or default-branch commit in 2026+. Older repos are excluded
   regardless of historical prestige.
4. **No self-written trainers / inference loops.** Including: hand-rolled
   PPO/GRPO trainers, custom KV-cache management, custom mixed-precision
   or distributed-training scaffolding. Wrap an existing framework.
5. **Paper-released code allowed** when the repo meets the 2026+ bar and
   the paper appears in `research/LITERATURE_GROUNDING.json`.
6. **Write `research/INFRA_SHORTLIST.md`** during the research stage with
   every candidate you considered (URL + last release/commit date + paper
   if any + one-line fit rationale + maintained-yes/no).
7. **Write `research/INFRA_CHOICE.md`** during the plan stage locking in
   exactly one training framework and exactly one inference framework
   with rationale tying the choice to the project domain and the GPU /
   API budget. Mirror the same locked choice in
   `research/EXPERIMENT_PLAN.md` under an `## Infra` section.
8. **Skip both artifacts only if** the project does not train any model
   and does not run large-scale inference (e.g. pure literature analysis).
   Record that skip explicitly in `research/RESEARCH_BRIEF.md`.

The L2 reviewer ticks these checklist items as `research.infra_shortlist`
and `plan.infra_choice`. Empty / hand-waved infra sections fail the gate.

## When to use
- The operator asks for an EMNLP/ACL-style paper plan, research plan, experiment roadmap, or agent-science hypothesis.
- The task mentions turning a topic seed or paper/code trend into experiments, baselines, ablations, metrics, or a paper-ready evidence plan.
- The project already has a research profile, benchmark harness, or prior experiment artifacts that need to become a coherent roadmap.
- The agent needs to decide what evidence must exist before any paper claim can be written.

## When NOT to use
- The operator only asks for a small coding fix with no research or experiment component.
- Raw benchmark results already exist and the task is only to analyze/plot them; use a results-analysis or paper-writing skill instead.
- The operator only wants an exhaustive standalone literature survey with no experiment planning. If external scholarly access is unavailable during planning, use this skill and mark literature/source search as a blocker.

## How to solve
1. Locate or create a research workspace:
   - Prefer existing `research/`, `experiments/`, `benchmarks/`, `paper/`, or project-specific profile files.
   - If no brief exists, write `research/RESEARCH_BRIEF.md` from the operator objective and clearly label assumptions.
   - Treat the brief as a seed and constraints file only. Do not let the agent invent the final idea, contribution, benchmark, or paper thesis before literature/code discovery.
   - If this is part of a full auto-research mission, create or update `research/PIPELINE_STATE.json` with `current_stage: "plan"` and do not mark the plan stage `ready` until the artifacts below exist.

2. Extract the seed research contract:
   - Broad problem area, target venue, allowed resources, and constraints.
   - Candidate task family, likely evidence type, expected failure modes, and what would make the mission not worth pursuing.
   - Defer the main hypothesis and final contribution until after the literature/code grounding steps below.
   - Constraints: compute, model/API availability, time budget, datasets, and benchmark licenses.
   - Record local GPU capability only in internal planning artifacts and use it to choose the strongest feasible training setup. If the workspace has large GPUs, do not default to a tiny custom scorer; justify any smaller model as a baseline, ablation, or operator-approved scope change. Do not plan to copy local device ordinals, CUDA variables, cache paths, workstation names, or Argus/Codex route configuration into the manuscript.

3. Run literature and specified-source grounding before locking the plan:
   - Write `research/LITERATURE_REVIEW.md`, `research/LIT_MATRIX.tsv`, and `research/LITERATURE_GROUNDING.json` before finalizing hypotheses. Target 10 recent high-quality papers from the current system year when credible sources exist; otherwise include the strongest recent papers from the previous two years and record the current-year shortfall.
   - Include at least 3 classic anchor papers that define the task, benchmark, evaluation protocol, or method family. A hot news topic without classic anchors is not ready for EMNLP planning.
   - Use scholarly sources first: ACL Anthology, arXiv, Semantic Scholar, OpenReview, Papers with Code, official conference award/program pages, and benchmark/dataset project pages.
   - Write `research/SOURCE_DISCOVERY.md` for non-peer-reviewed trend sources. Include operator-specified sources such as 机器之心 and 新智元, plus official lab/blog posts when useful.
   - Treat media posts as discovery signals only. They may suggest hot topics, systems, authors, datasets, pain points, or code releases. They do not need paper/benchmark/code backing to be recorded, but they cannot by themselves support paper claims.
   - First record source access status: direct URL tried, HTTP result, date, accessible fallback, and whether the source is usable. For 新智元, try the currently accessible official site (`aiera.com.cn`) before marking the source blocked; for 机器之心, record whether article/search pages or only the data-service page are reachable.
   - Write `research/TREND_INSIGHTS.md` as a decision artifact, not a news digest. Extract repeated pain points, emerging evaluation settings, datasets/tools people care about, surprising practitioner constraints, unresolved questions, and hype claims that require verification.
   - Convert each useful trend into a testable research question with possible benchmark, baseline implication, cost/risk estimate, and decision: `use`, `watch`, `reject`, or `needs-scholarly-grounding-for-claim`.
   - Do not spend model/API budget on benchmark runs until at least one candidate research question is backed by both the literature matrix and a usable trend insight or an explicit reason why the trend scan is unavailable.
   - Never copy paper or media prose into artifacts. Store metadata, URLs, short paraphrased summaries, and your own analysis.
   - `research/LITERATURE_GROUNDING.json` must contain `recent_high_quality_papers` (minimum 10), `classic_papers` (minimum 3), and `trend_sources` with source name, URL, access date, and extracted signals. Trend sources do not need paper/benchmark/code backing and do not require `paper_or_benchmark_backing`; if a trend later becomes a technical paper claim, the claim must be supported by surveyed papers/code/benchmarks or local experiment artifacts. The literature matrix must include: source type, date, title, venue/status, URL, task, method, dataset, baseline, metric, key result, limitation, and implication for this project.

4. Derive candidate ideas from evidence, not brainstorming:
   - Each candidate must cite `source_refs` from surveyed recent papers, classic papers, benchmarks, official projects, or code releases. The selected idea must have at least 2 `derived_from` references and a concrete `research_gap`, `novelty_delta`, and `selection_rationale`.
   - Each candidate must state the frontier comparison it would have to beat: named SOTA/strong baselines, expected benchmark, primary metric, and why the improvement would be publishable rather than cosmetic.
   - If the only source is the agent's own intuition, set the gate to `blocked` or continue literature search; do not manufacture an idea.
   - **Anti-mediocrity gate**: reject an idea if ANY of these apply:
     * It is a minor variant of an existing method (e.g., "add a memory module" to an agent that already has memory)
     * The novelty is only "combining X and Y" without a clear reason why the combination solves an unsolved problem
     * No paper in the literature survey left this gap open — the "gap" is manufactured
     * The expected improvement over SOTA is <2% on the primary metric with no qualitative novelty
     * A trivial baseline (prompt engineering, simple heuristic) could plausibly match the proposed method
   - Write `research/IDEA_REJECTION_LOG.md` with each rejected candidate and the specific anti-mediocrity reason. A project with 0 rejections is suspicious.

5. Survey reusable code and download reference implementations:
   - Search official paper code, benchmark repositories, Papers with Code links, GitHub project pages, dataset repos, and well-licensed libraries related to the selected idea.
   - **Download and study reference implementations**: for the top-3 most relevant papers, clone their official code repos into `code/references/` (shallow clone: `git clone --depth 1`). Read their model architecture, training loop, evaluation scripts, and data processing. Record what you learned in `research/CODE_STUDY_NOTES.md`.
   - This is NOT optional. You cannot design a competitive method without understanding how existing methods actually work. Reading the paper abstract is not enough — you must read the code.
   - For each downloaded repo, record: paper title, repo URL, license, what you learned about the method, what code/ideas you can reuse, and what limitations you found.
   - Prefer license-compatible official paper code, benchmark harnesses, and libraries over writing everything from scratch. If all external code is rejected, record `from_scratch_justification` or `no_usable_external_code_reason`.
   - Never paste incompatible or unlicensed code; record attribution for any reused/adapted source.

6. Map novelty and blockers:
   - Write `research/NOVELTY_MAP.md` showing what is new relative to each close paper/source.
   - Write `research/RELATED_WORK_BLOCKERS.md` for papers or trend reports that already solve the idea, expose missing baselines, or make the planned benchmark insufficient.
   - If the idea is already solved or only differs cosmetically, set the planning decision to `pivot` or `rejected` instead of continuing.

7. Choose benchmark sources from existing real benchmarks:
   - Treat benchmark selection as part of the literature/code survey. Search recent/frontier and widely used benchmarks from papers and official repos, including ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web-style web tasks, GAIA-style assistant tasks, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, and domain-specific ACL Anthology benchmarks.
   - Hard requirement: final paper evidence must use existing real benchmark sources, official datasets, or official task releases with documented ground truth/evaluation. Do not invent local synthetic tasks, synthetic proxies, generated episodes, or hand-written gold graphs for the main claim.
   - If no real benchmark can test the idea, pivot the idea or mark the project blocked. Do not fill the gap with a synthetic benchmark and call it EMNLP-ready.
   - Do not plan a final EMNLP evidence package around one benchmark source. Select a diverse benchmark mix: at least 3 independent practical/frontier benchmark suites, official task releases, or source families are the hard minimum for final evidence. Same-family variants of one suite count as one source. The selected mix should cover distinct capabilities, domains, or failure modes so the paper can argue method effectiveness beyond one dataset family. Planned diagnostic rows do not count until executed and scored.
   - Plan the full EMNLP evidence run as a complete multi-source matrix before final drafting: every required method/baseline condition should have raw scored rows on the selected benchmark families, sampled/adapted from documented public benchmark splits when licenses and cost allow.
   - Hard prohibition: benchmark scale cannot be achieved by copying a 50/60-task pilot, changing IDs, adding suffixes such as `_r2`/`_copy`, duplicating rows, or reusing the same prompts/specs/gold answers as new episodes.
   - Record benchmark provenance in the plan with a **Selected benchmark sources** table/list: each selected benchmark/component must include name, URL/repo, paper/citation/DOI, version/date, license/access, unique task count contributed, split/filtering, why it is practical/frontier, what capability/failure mode it tests, surveyed benchmark alternatives, and why this selected mix fits EMNLP.
   - Synthetic/local tasks are permitted only as engineering smoke tests and must be labeled `smoke_only: true`; their results must not appear as main paper evidence, headline numbers, final tables, or submission-readiness support.

8. Design baselines and ablations:
   - Include a bare-agent baseline, the strongest relevant literature/SOTA baselines that are feasible to run or faithfully reproduce, and the proposed trained/hybrid method.
   - **Strong baseline requirement**: at least ONE baseline must be a reproduced or faithfully re-implemented version of a recent published method (not just a no-skill/random/lexical baseline). Download the official code (step 5) and run it on your benchmarks. If the official code cannot run, re-implement the core algorithm and verify your reproduction matches reported numbers within reasonable tolerance. Record the reproduction result in `research/BASELINE_REPRODUCTION.md`.
   - **Why this matters**: if you only compare against trivial baselines (no-skill, random, BM25), any method looks good. EMNLP reviewers will reject a paper that avoids comparing to relevant published methods.
   - Write `research/BASELINE_AND_BENCHMARK_PLAN.md` with each required baseline discovered from literature or specified sources. Mark each as `required`, `optional`, or `blocked` with a reason and artifact path.
   - Include ablations that isolate the trained backbone/adaptation, data source, retrieval/planning/controller component, auxiliary heads, and compute budget when relevant. A tiny scorer cannot stand in as the proposed method if the project has enough GPU budget for a stronger backbone.
   - For each cell, name the exact command or harness that should run.

9. Define evidence contracts:
   - Required raw artifacts: `experiments/<run_id>/manifest.json`, stdout/stderr logs, result JSON/TSV, git status or source manifest, started/ended timestamps, model IDs, token/cost counters.
   - Required live-observability artifacts for long or model-call-heavy experiments: `status.json`, `progress.jsonl`, `pid`, `stdout.log`, `stderr.log`, and a `STOP` cancellation file contract.
   - Required derived artifacts: tables, plots, failure taxonomy, and a claims-evidence matrix.
   - Anti-fabrication rule: every future claim must cite a raw local artifact path.

10. Write `research/EXPERIMENT_PLAN.md`:
   - Include a run matrix table: id, hypothesis, command, expected output, metric, budget, priority.
   - Include a staged order: smoke -> pilot -> full run -> ablation -> paper analysis.
   - Mark MUST-RUN vs NICE-TO-HAVE and explicitly state what can be skipped if budget is tight.
   - Include the project-local model/data cache contract for every training or dataset command: `HF_HOME=$(pwd)/models/huggingface`, `HUGGINGFACE_HUB_CACHE=$(pwd)/models/huggingface/hub`, `HF_DATASETS_CACHE=$(pwd)/models/huggingface/datasets`, `TRANSFORMERS_CACHE=$(pwd)/models/huggingface/hub`, and `TORCH_HOME=$(pwd)/models/torch`; each project owns its weights under `./models/` (see the training-infrastructure-guide skill).
   - Include an "Observability and cancellation" section: expected trial count, how progress is streamed, how the user cancels, and which invariants trigger agent-initiated early stop.
   - Include a "Benchmark provenance" section. A plan without benchmark provenance is incomplete for EMNLP-style empirical work.

11. Write `research/CLAIMS_TO_TEST.md`:
   - One candidate paper claim per row.
   - Required evidence path(s).
   - Current status: `missing`, `running`, `supported`, `weakened`, or `rejected`.

12. Write the planning gate decision:
   - Write `research/GO_NO_GO.md` with verdict `go`, `blocked`, `pivot`, or `rejected`.
   - Use `blocked` when compute, credentials, benchmark access, or literature access prevents a faithful experiment.
   - Use `pivot` or `rejected` when the research question is not testable with available benchmarks instead of drafting a weak paper.

13. Verify the plan:
   - Ensure every planned paper claim maps to at least one concrete run.
   - Ensure every run has an expected output path and a success/failure criterion.
   - Ensure no result number appears unless it already exists in a cited artifact.
   - Self-audit the literature-grounding requirement; do not mark the plan stage ready while review finds missing recent papers, classic papers, or trend-source metadata.
   - Self-audit the idea-provenance requirement and the code-reuse requirement; do not mark the plan stage ready while the idea looks agent-generated or the implementation ignores surveyed paper/open-source code.

## Response shape
- End with a short list of the next executable missions, each with acceptance criteria.
