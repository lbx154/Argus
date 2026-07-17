---
name: Research Brief To Experiment Plan
description: Convert an operator research seed into a literature/code-derived, falsifiable AI research plan supporting method, systems, theory, diagnostic, characterization, evaluation, data, positive, negative, and boundary contributions.
category: research-planning
version: 1
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Research Brief To Experiment Plan

## Description
Turn a loose operator research direction into a concrete, evidence-first experiment plan. The final idea must come from surveyed papers, trend signals, benchmark gaps, and reusable code sources--not from agent brainstorming. Adapted from ARIS-style research pipeline concepts, but written for argus-skill's single-mission engineer/reviewer loop instead of slash-command skill chaining.

## Non-negotiable research bar
- The selected project must be a frontier-domain project, not a toy mechanism study. Before locking the idea, identify current strong papers, current benchmark leaderboards or reported SOTA baselines, and the concrete gap that remains open.
- **Research taste**: the idea must contain a genuine insight or surprising angle. Ask yourself:
  - "What would make a reviewer in the selected venue say 'that's interesting,
    I hadn't thought of it that way'?"
  - "What is the ONE key insight that makes this work, and why hasn't anyone done it before?"
  - If you can't answer these, the idea needs more thinking, not more engineering.
  - A paper that says "we applied technique A to domain B and it worked" is NOT research — there must be a WHY.
- Match the contribution shape to the question. Training-based methods, systems
  mechanisms, theory, interpretability, diagnostic/characterization studies,
  evaluation work, and data contributions are all valid when they provide a
  non-trivial, falsifiable insight.
- Use the strongest model/system scale needed to answer the question, not the
  largest model the machine can fit. Record compute and model details when they
  are scientifically relevant.
- Final empirical evidence must include an appropriate public benchmark,
  dataset, task suite, challenge, or official evaluation release. Synthetic or
  generated data may support controlled diagnostics, causal isolation, stress
  tests, or ablations, but must not be the sole final empirical evidence.

## Training & inference infrastructure contract (plan stage, after idea de-risk)

Do not spend the research stage surveying generic frameworks before the idea's
binding premise survives a real falsification probe. Once it does, the plan must
lock maintained open-source training/inference infrastructure instead of
inventing custom loops.

1. **Read `argus_builtin_skills/training-infrastructure-guide.md` first.**
   It is the bundled curated baseline (LLM SFT/DPO/RLHF, agent RL,
   diffusion, LLM inference, API inference). Anchor your selection there.
2. **Search only as needed for the surviving method.** Compare credible
   candidates that materially differ for this workload. Reuse previously
   certified framework evidence when current; do not clone a quota.
3. **Maintenance bar.** The selected project must be actively maintained and
   compatible with the required model/method/hardware. A calendar-year cutoff
   is not a substitute for compatibility or maintenance evidence.
4. **Reuse infrastructure when it is not the contribution.** Prefer maintained
   frameworks for standard training/inference, but custom trainers, evaluators,
   runtimes, cache policies, kernels, or distributed mechanisms are allowed when
   they are necessary to test the research contribution. State why existing
   infrastructure is insufficient and validate the custom path against a
   trusted reference.
5. **Paper-released code allowed** when the repository is maintained,
   method-compatible, and its paper appears in the canonical literature ledger.
6. **Write `research/INFRA_CHOICE.md`** during the plan stage with a short
   comparison of the credible candidates considered, then lock in
   exactly one training framework and exactly one inference framework
   with rationale tying the choice to the project domain and the GPU /
   API budget. Mirror the same locked choice in
   `research/EXPERIMENT_PLAN.md` under an `## Infra` section.
7. **Skip the artifact only if** the project does not train any model
   and does not run large-scale inference (e.g. pure literature analysis).
   Record that skip explicitly in `research/EXPERIMENT_PLAN.md`.

The L2 reviewer checks `plan.infra_choice`. Empty / hand-waved choices fail;
generic pre-idea framework surveys are not research-stage progress.

## When to use
- The operator asks for an AI research paper plan, experiment roadmap, or
  falsifiable research hypothesis in any AI subfield.
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

   - If the operator did not specify a target venue, use live search to select a
     domain-appropriate CCF-A conference whose relevant submission deadline has
     not passed at the current UTC time. Verify the CCF classification, scope,
     deadline/time zone, and official author kit from primary sources. Write
     `research/VENUE_SELECTION.md`, set the descriptive `target_venue`, and write
     `research/VENUE_PROFILE.json`; do not silently assume EMNLP.

3. Run literature and specified-source grounding before locking the plan:
   - Author **one canonical ledger**, `research/LITERATURE_GROUNDING.json`, before finalizing hypotheses. Include enough primary sources to cover every material premise: the nearest competing methods, relevant foundations/classic anchors, contradictory or negative evidence, and the unresolved frontier. Coverage is claim-driven, not a fixed paper count.
   - Generate `research/LIT_MATRIX.tsv` mechanically with `python -m argus_skill.verticals.research.literature_ledger sync --project-root .`; never ask a model to maintain the same paper metadata independently in JSON and TSV.
   - Put the connected founding-work → turning-points → nearest-SOTA → open-frontier synthesis directly in `research/RESEARCH_BRIEF.md`. A separate `LITERATURE_REVIEW.md` or `RESEARCH_TIMELINE.md` is optional, not a gate.
   - Use scholarly sources first: ACL Anthology, arXiv, Semantic Scholar, OpenReview, Papers with Code, official conference award/program pages, and benchmark/dataset project pages.
   - Record useful non-peer-reviewed trend sources in the canonical ledger and summarize only decision-relevant signals in the brief. Do not create a separate news digest merely to satisfy a filename.
   - Treat media posts as discovery signals only. They may suggest hot topics, systems, authors, datasets, pain points, or code releases. They do not need paper/benchmark/code backing to be recorded, but they cannot by themselves support paper claims.
   - First record source access status: direct URL tried, HTTP result, date, accessible fallback, and whether the source is usable. For 新智元, try the currently accessible official site (`aiera.com.cn`) before marking the source blocked; for 机器之心, record whether article/search pages or only the data-service page are reachable.
   - Convert each useful trend into a testable research question in the brief with a benchmark/baseline implication, cost/risk estimate, and decision: `use`, `watch`, `reject`, or `needs-scholarly-grounding-for-claim`.
   - Do not spend model/API budget on benchmark runs until at least one candidate research question is backed by both the literature matrix and a usable trend insight or an explicit reason why the trend scan is unavailable.
   - Never copy paper or media prose into artifacts. Store metadata, URLs, short paraphrased summaries, and your own analysis.
   - The canonical ledger must retain title, primary URL, source provenance, and project implication for every paper. Trend sources do not need paper/benchmark/code backing, but any trend promoted into a technical claim must be supported by primary literature/code/benchmarks or local experiment artifacts. Run `literature_ledger check` for identity/source-shape errors; the Reviewer, not a quota, judges coverage.

4. Derive candidate ideas from evidence, not brainstorming:
   - Each candidate must cite `source_refs` from surveyed recent papers, classic papers, benchmarks, official projects, or code releases. The selected idea must have at least 2 `derived_from` references and a concrete `research_gap`, `novelty_delta`, and `selection_rationale`.
   - Each candidate must state the strongest relevant frontier comparison,
     expected public evidence source, decisive outcome, and why a positive,
     negative, diagnostic, or boundary result would matter.
   - If the only source is the agent's own intuition, set the gate to `blocked` or continue literature search; do not manufacture an idea.
   - **Research-value gate**: reject or revise an idea if ANY of these apply:
     * It has no important question, mechanism insight, reliable characterization,
       useful benchmark/data contribution, or decision-relevant finding. A
       diagnostic, taxonomy, evaluation, or negative-result project is valid when
       it changes understanding or practice and has a defensible evidence plan.
     * It is a minor variant of an existing method (e.g., "add a memory module" to an agent that already has memory)
     * The novelty is only "combining X and Y" without a clear reason why the combination solves an unsolved problem
     * No paper in the literature survey left this gap open — the "gap" is manufactured
     * The evidence plan cannot distinguish the claimed explanation from a
       plausible alternative or confound
     * The project cannot be tested within the operator's available resources and
       has no credible staged, sampled, analytical, or collaborative execution plan
   - Write `research/IDEA_REJECTION_LOG.md` with each rejected candidate and the specific anti-mediocrity reason. A project with 0 rejections is suspicious.

5. Survey reusable code and download reference implementations:
   - Search official paper code, benchmark repositories, Papers with Code links, GitHub project pages, dataset repos, and well-licensed libraries related to the selected idea.
   - **Download and study reference implementations** for the papers whose code will actually be reused, reproduced, or used as a strong baseline. Clone those official repos into `code/references/` (shallow clone: `git clone --depth 1`). Read the exact entrypoints relevant to this project and record what you learned in `research/CODE_STUDY_NOTES.md`; do not clone an arbitrary quota of repos.
   - This is NOT optional. You cannot design a competitive method without understanding how existing methods actually work. Reading the paper abstract is not enough — you must read the code.
   - For each downloaded repo, record: paper title, repo URL, license, what you learned about the method, what code/ideas you can reuse, and what limitations you found.
   - Prefer license-compatible official paper code, benchmark harnesses, and libraries over writing everything from scratch. If all external code is rejected, record `from_scratch_justification` or `no_usable_external_code_reason`.
   - Never paste incompatible or unlicensed code; record attribution for any reused/adapted source.

6. Map novelty and blockers:
   - Write `research/NOVELTY_MAP.md` showing what is new relative to each close paper/source.
   - Write `research/RELATED_WORK_BLOCKERS.md` for papers or trend reports that already solve the idea, expose missing baselines, or make the planned benchmark insufficient.
   - If the idea is already solved or only differs cosmetically, set the planning decision to `pivot` or `rejected` instead of continuing.

7. Choose benchmark sources from public research artifacts:
   - Treat benchmark selection as part of the literature/code survey. Search the
     public benchmarks, datasets, challenge suites, official task releases, or
     standard problem collections used by the closest work in the actual domain.
   - Hard requirement: final empirical evidence must execute on at least one
     appropriate public source with documented provenance and evaluation
     semantics. Do not invent a local benchmark and present it as public evidence.
   - Synthetic or generated tasks may supplement the public evidence for
     controlled diagnostics, mechanism isolation, stress tests, or ablations.
     Label them explicitly and keep them separate from headline public-benchmark
     results.
   - Choose the number of public sources, tasks, seeds, models, and conditions
     from the scope of the claim and the required statistical power. There is no
     universal three-source or fixed task-count minimum.
   - Plan a complete claim-relevant execution matrix before final drafting:
     every condition needed for the stated conclusion should have raw evidence or
     an explicit, justified exclusion.
   - Hard prohibition: benchmark scale cannot be achieved by copying a 50/60-task pilot, changing IDs, adding suffixes such as `_r2`/`_copy`, duplicating rows, or reusing the same prompts/specs/gold answers as new episodes.
   - Record benchmark provenance in the plan with a **Selected public evidence
     sources** table/list: name, official URL/repo, paper/citation/DOI,
     version/date, license/access, split/filtering, evaluation unit, metric, claim
     tested, and rationale for the selected scope.
   - Synthetic/local tasks are permitted only as engineering smoke tests and must be labeled `smoke_only: true`; their results must not appear as main paper evidence, headline numbers, final tables, or submission-readiness support.

8. Design baselines and ablations:
   - Include the strongest relevant literature, standard, or system baselines
     needed to interpret the contribution. Do not require an arbitrary baseline
     count or assume the contribution is a trained agent method.
   - **Strong baseline requirement**: at least ONE baseline must be a reproduced or faithfully re-implemented version of a recent published method (not just a no-skill/random/lexical baseline). Download the official code (step 5) and run it on your benchmarks. If the official code cannot run, re-implement the core algorithm and verify your reproduction matches reported numbers within reasonable tolerance. Record the reproduction result in `research/BASELINE_REPRODUCTION.md`.
   - **Why this matters**: trivial comparisons cannot establish a meaningful
     positive, negative, diagnostic, or boundary conclusion.
   - Write `research/BASELINE_AND_BENCHMARK_PLAN.md` with each required baseline discovered from literature or specified sources. Mark each as `required`, `optional`, or `blocked` with a reason and artifact path.
   - Include controls or ablations that isolate the actual mechanism, data,
     algorithm, system component, or explanatory factor when relevant.
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
   - Include a "Public evidence provenance" section. An empirical plan without
     public benchmark/data provenance is incomplete.

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
   - Self-audit claim coverage in the canonical literature ledger; do not mark the plan stage ready while a material premise, nearest competitor, relevant foundation, or contradictory result is unsupported.
   - Self-audit the idea-provenance requirement and the code-reuse requirement; do not mark the plan stage ready while the idea looks agent-generated or the implementation ignores surveyed paper/open-source code.

## Response shape
- End with a short list of the next executable missions, each with acceptance criteria.
