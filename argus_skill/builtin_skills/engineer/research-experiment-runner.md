---
name: Research Experiment Runner
description: Execute reproducible AI research experiments across model, systems, data, evaluation, interpretability, and diagnostic studies using public benchmarks, claim-proportional evidence, raw artifacts, and honest positive or negative outcomes.
category: research-execution
priority: high
version: 1
created_at: 2026-07-17T00:00:00+00:00
---

# Research Experiment Runner

## Purpose

Execute the experiment plan without forcing every AI project into an
LLM-agent, RL, multi-benchmark, or GPU-training shape. The experiment design,
scale, baselines, uncertainty analysis, and compute path must follow the actual
research claim.

This skill supports:

- learned model or representation methods;
- inference, serving, compiler, memory, and systems work;
- interpretability, diagnostic, characterization, and evaluation studies;
- data, benchmark, robustness, and failure-analysis work;
- empirical support for theoretical or algorithmic contributions.

## Evidence policy

1. **Use public evidence.** Every final empirical paper must evaluate on at least
   one relevant public benchmark, dataset, task suite, challenge, or official
   evaluation release with documented provenance and evaluation semantics.
2. **Do not invent the final benchmark.** Locally generated or synthetic data may
   be used for smoke tests, controlled diagnostics, causal isolation, stress
   tests, or ablations, but it must be labeled as supplementary evidence and
   must not be the sole support for a paper-facing empirical claim.
3. **Scale follows the claim.** There is no universal benchmark-family count,
   task-count minimum, seed count, or condition matrix. Use enough independent
   evidence and uncertainty analysis to support the exact scope of the claim.
   Broader claims require broader validation; narrow mechanism claims may need a
   focused public benchmark plus decisive controls.
4. **Use the strongest relevant comparisons.** Reproduce or faithfully compare
   against the closest feasible published baseline, standard method, or accepted
   reference implementation. Do not pad the matrix with arbitrary weak baselines.
5. **Positive results are not required.** A supported improvement, a clean null,
   a boundary condition, a failure mechanism, or a diagnostic result may all be
   publishable when the question is important and the evidence changes
   understanding or practice.

## Before execution

1. Read `research/EXPERIMENT_PLAN.md`, `research/GO_NO_GO.md`, and the active
   stage checklist.
2. Confirm the public benchmark/data source, version, split, license/access
   conditions, official metric or evaluator, and any permitted filtering.
3. Reproduce the strongest feasible reference condition before interpreting the
   proposed method.
4. Run the Environment Readiness Gate only for resources the experiment
   actually uses:
   - verify CUDA and framework imports for GPU work;
   - verify CPU/runtime/compiler dependencies for CPU or systems work;
   - verify data access for dataset studies;
   - verify API routes only when the experiment calls them.
5. Freeze claim-relevant settings in a manifest or run contract appropriate to
   the method. LLM/RL-specific fields are required only for LLM/RL experiments.

## Run artifact contract

Each substantive run should have a stable run directory containing:

- `manifest.json`: objective, source revision, command/config, public
  benchmark/data provenance, method/baseline identity, budget, and expected
  outputs;
- `status.json`: current and terminal state;
- raw observations or scored rows in JSONL/TSV/CSV or the domain-native format;
- stdout/stderr or equivalent logs;
- progress/heartbeat data for long runs;
- an explicit cancellation mechanism when the job is long-running;
- a short `RUN_REPORT.md` after collection.

The exact schema may vary by domain. Do not manufacture per-task rows for work
whose natural evidence is a trace, profile, theorem check, aggregate simulation,
human cohort, or systems measurement.

## Execution loop

0. **Build the project research platform.** The Engineer may create the
   project-local environment, data/model bindings, evaluator, runner, telemetry,
   and teardown tooling needed by the research question. Record faithful probes
   in `research/PLATFORM_SPEC.json` and run
   `python -m argus_skill.tools.research_platform doctor --project-root .`.
   Platform failures route back to Engineer repair and are not evidence about
   the scientific idea.
1. **Smoke the real path.** Run the smallest faithful end-to-end public-data or
   official-evaluator check that catches wiring errors.
2. **Execute the preregistered comparison.** Keep data, metric, compute, and
   stopping conditions fair across the proposed method and baselines.
   Launch every long/GPU command through the durable subagent interface; raw
   attached shell launches are an orchestration defect because their owner and
   terminalizer disappear with the model session.
   Separate the full-cohort quality pass from the resource microbenchmark when
   they answer different questions. Batch homogeneous examples when batching
   preserves outputs, and apply the same execution implementation to compared
   conditions for resource claims. Do not repeat an entire deterministic cohort
   merely to estimate runtime when a declared representative resource sample is
   sufficient.
   For expensive repeats, predeclare `min_repeats`, `max_repeats`, minimum
   meaningful effect, and interval precision, then use
   `python -m argus_skill.tools.experiment_efficiency` after each repeat. Stop on
   supported effect, futility, sufficient precision, or the declared maximum;
   retain every observation regardless of the stopping decision.
3. **Monitor without steering toward success.** Preserve crashes, nulls,
   exclusions, and failed cases. Do not change thresholds or remove difficult
   examples after seeing results.
4. **Use background execution when useful.** Long jobs may run through the
   supervised subagent system, a scheduler, or the project's native runner.
   Parallelism is optional and must follow real resource and file-ownership
   boundaries.
5. **Collect and verify.** Recompute headline aggregates from raw artifacts,
   check row/trace counts against the manifest, and preserve the exact evaluator
   output.
6. **Classify the result.**
   - `supported_positive`: evidence supports the proposed improvement;
   - `supported_negative`: a clean null or failure answers the research question;
   - `supported_boundary`: the mechanism works only in a scoped regime;
   - `misconfigured_run`: repair and rerun because the evidence is invalid;
   - `inconclusive`: evidence cannot answer the question;
   - `infeasible_under_budget`: a fair test is outside the current allocation.

Only `misconfigured_run` justifies an automatic corrective rerun. A clean
negative result is evidence, not a pipeline failure.

## Public benchmark provenance

Record for every selected public source:

- canonical name and official URL/repository;
- paper/DOI/citation when available;
- version/date and split;
- license or access conditions;
- evaluation unit and official metric/evaluator;
- any sampling/filtering or conversion performed locally;
- the claim or failure mode this source tests;
- raw artifact paths proving execution.

Synthetic or generated diagnostics must be clearly separated from this table.

## Completion criteria

The run stage is ready for review when:

- every claim-relevant planned condition has executed or has an explicit,
  evidence-backed exclusion;
- at least one appropriate public benchmark/data/task source has real executed
  evidence for empirical claims;
- the strongest relevant baseline is represented fairly;
- raw artifacts regenerate the reported aggregates;
- uncertainty, variance, or repeatability is handled in a way appropriate to the
  data-generating process;
- positive, negative, and contradictory outcomes are all retained;
- the result scope does not exceed the evidence.

## Response shape

- Name the run(s), public evidence source(s), and executed conditions.
- Report the result classification and the strongest supported claim.
- List raw artifacts and the command used to regenerate headline numbers.
- State remaining uncertainty without turning it into a success-shaped fallback.
