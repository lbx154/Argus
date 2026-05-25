---
name: Research Brief To Experiment Plan
description: Convert an operator research seed into a literature/code-derived idea and falsifiable experiment plan with hypotheses, baselines, metrics, budgets, and artifact contracts.
category: research-planning
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Research Brief To Experiment Plan

## Description
Turn a loose operator research direction into a concrete, evidence-first experiment plan. The final idea must come from surveyed papers, trend signals, benchmark gaps, and reusable code sources--not from agent brainstorming. Adapted from ARIS-style research pipeline concepts, but written for argus-skill's single-mission engineer/reviewer loop instead of slash-command skill chaining.

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
   - Write `research/IDEA_PROVENANCE.json` after the literature and source-discovery artifacts exist.
   - Include `idea_generation_mode: "literature_and_code_grounded"` or `paper_derived`, `not_agent_brainstorm: true`, at least 3 `candidate_ideas`, and a `selected_idea`.
   - Each candidate must cite `source_refs` from surveyed recent papers, classic papers, benchmarks, official projects, or code releases. The selected idea must have at least 2 `derived_from` references and a concrete `research_gap`, `novelty_delta`, and `selection_rationale`.
   - If the only source is the agent's own intuition, set the gate to `blocked` or continue literature search; do not manufacture an idea.

5. Survey reusable code before implementation:
   - Search official paper code, benchmark repositories, Papers with Code links, GitHub project pages, dataset repos, and well-licensed libraries related to the selected idea.
   - Write `research/CODE_REUSE_PLAN.json` with `searched_queries`, `code_sources`, URL, source type, paper/project backing, license/terms, attribution, and reuse decision: `use`, `adapt`, `fork`, `reference`, `baseline`, or `reject`.
   - Prefer license-compatible official paper code, benchmark harnesses, and libraries over writing everything from scratch. If all external code is rejected, record `from_scratch_justification` or `no_usable_external_code_reason`.
   - Never paste incompatible or unlicensed code; record attribution for any reused/adapted source.

6. Map novelty and blockers:
   - Write `research/NOVELTY_MAP.md` showing what is new relative to each close paper/source.
   - Write `research/RELATED_WORK_BLOCKERS.md` for papers or trend reports that already solve the idea, expose missing baselines, or make the planned benchmark insufficient.
   - If the idea is already solved or only differs cosmetically, set the planning decision to `pivot` or `rejected` instead of continuing.

7. Choose benchmark sources before inventing synthetic tasks:
   - Treat benchmark selection as part of the literature/code survey. Before inventing local tasks, search recent/frontier and widely used benchmarks from papers and official repos, including ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web-style web tasks, GAIA-style assistant tasks, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, and domain-specific ACL Anthology benchmarks.
   - Prefer established public benchmarks used by agent/NLP papers when feasible; only choose synthetic tasks after recording why the surveyed public benchmarks are infeasible or insufficient for the claim.
   - Plan the full EMNLP evidence run at 240/250 scale: at least 240 unique semantic scored main tasks/episodes before final drafting. For a `research.md` synthetic benchmark, generate distinct episodes across the 5 families x 3 difficulties design to reach 240+; for public benchmarks, sample/adapt a documented 240+ task split when licenses and cost allow.
   - Hard prohibition: benchmark scale cannot be achieved by copying a 50/60-task pilot, changing IDs, adding suffixes such as `_r2`/`_copy`, duplicating rows, or reusing the same prompts/specs/gold answers as new episodes.
   - Record benchmark provenance in the plan: URL, paper/citation, version/date, license/access, unique task count, split, filtering, surveyed benchmark alternatives, and why the selected source fits EMNLP.
   - If using synthetic tasks, justify why public benchmarks are infeasible for this run, follow `research.md` construction rules, preserve the generator/sampler, include a uniqueness audit, and treat any <240-task result as a pilot with a required scale-up/public-validation follow-up.

8. Design baselines and ablations:
   - Include a bare-agent baseline, any existing system baseline, and the proposed argus-skill variant.
   - Write `research/BASELINE_AND_BENCHMARK_PLAN.md` with each required baseline discovered from literature or specified sources. Mark each as `required`, `optional`, or `blocked` with a reason and artifact path.
   - Include ablations that isolate skill memory, reviewer gate, planner/critic, daemon continuity, and budget controls when relevant.
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
   - Update `research/PIPELINE_STATE.json` so the plan stage is `ready` only when `research/IDEA_PROVENANCE.json`, `research/CODE_REUSE_PLAN.json`, `research/EXPERIMENT_PLAN.md`, `research/CLAIMS_TO_TEST.md`, `research/BASELINE_AND_BENCHMARK_PLAN.md`, and `experiments/BENCHMARK_PROVENANCE.md` are present.

13. Verify the plan:
   - Ensure every planned paper claim maps to at least one concrete run.
   - Ensure every run has an expected output path and a success/failure criterion.
   - Ensure no result number appears unless it already exists in a cited artifact.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .`; do not mark the plan stage ready while it reports missing recent papers, classic papers, or trend-source metadata.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-idea-provenance --project-root .` and `validate-code-reuse --project-root .`; do not mark the plan stage ready while the idea looks agent-generated or the implementation ignores surveyed paper/open-source code.

## Response shape
- Create or update `research/RESEARCH_BRIEF.md`, `research/LITERATURE_REVIEW.md`, `research/LIT_MATRIX.tsv`, `research/LITERATURE_GROUNDING.json`, `research/SOURCE_DISCOVERY.md`, `research/TREND_INSIGHTS.md`, `research/IDEA_PROVENANCE.json`, `research/CODE_REUSE_PLAN.json`, `research/NOVELTY_MAP.md`, `research/RELATED_WORK_BLOCKERS.md`, `research/BASELINE_AND_BENCHMARK_PLAN.md`, `research/EXPERIMENT_PLAN.md`, `research/CLAIMS_TO_TEST.md`, `research/GO_NO_GO.md`, and `research/PIPELINE_STATE.json`.
- End with a short list of the next executable missions, each with acceptance criteria.
