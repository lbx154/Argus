---
name: Auto Research Pipeline
description: "PRIMARY ENTRY POINT for full AI research projects across methods, systems, theory, interpretability, evaluation, data, diagnostics, and positive/negative/boundary findings. Orchestrates literature → plan → public evidence → execution → analysis → venue-aware paper → review → submission."
category: research-orchestration
priority: highest
version: 3
created_at: 2026-07-17T00:00:00+00:00
---

# Auto Research Pipeline

## Purpose

Run a complete AI research project as a gated, evidence-first workflow. This
pipeline is domain-neutral: agent/LLM research is one possible use case, not the
default shape.

Valid contribution shapes include:

- a new method, architecture, algorithm, objective, or system;
- theory or a formally supported mechanism;
- interpretability or causal analysis;
- a diagnostic, characterization, taxonomy, evaluation, or benchmark/data
  contribution;
- a rigorous negative result, null result, limitation, or boundary condition;
- a reproducible systems or efficiency finding.

The acceptance question is whether the work has research value and evidence
appropriate to its claim, not whether it reports a positive metric gain.

## Non-negotiable research bar

- Ground the question in primary literature, official artifacts, and the
  strongest relevant prior work.
- State one clear thesis or research question and the result that would support,
  weaken, or refute it.
- Preserve negative, contradictory, and failed evidence.
- Do not manufacture novelty, benchmarks, labels, results, citations, or
  provenance.
- Final empirical evidence must include at least one appropriate public
  benchmark, dataset, task suite, challenge, or official evaluation release.
- Synthetic/generated data may be used for smoke tests, controlled diagnostics,
  mechanism isolation, stress tests, and ablations, but must not be the sole
  final empirical evidence or be presented as a public benchmark.
- Evidence breadth and scale follow the claim. There is no universal benchmark
  count, task count, model count, seed count, effect-size threshold, or
  wall-clock cutoff.

## Venue selection

If the operator explicitly names a venue, use that venue and verify the current
cycle and official author kit.

If the venue is unspecified:

1. Use live web search at runtime.
2. Identify CCF-A conferences relevant to the paper's actual AI subfield whose
   main/research-track submission deadline has not passed at the current UTC
   time.
3. Verify CCF classification, scope, exact deadline/time zone, and official
   author kit from primary sources.
4. Write `research/VENUE_SELECTION.md` with candidates, sources, deadline
   status, scope fit, selection, and rejection reasons.
5. Set the descriptive `target_venue` field and write
   `research/VENUE_PROFILE.json`.

Never silently default to EMNLP, AAAI, or any closed conference. Venue-dependent
draft/review/submission work remains blocked until a current profile exists.

## Resource policy

- Discover available compute, APIs, data access, and time budget through the
  supported runtime helpers and project state; do not read or print raw secrets.
- Choose resources that answer the question faithfully. GPU availability does
  not require training a large model.
- Prefer maintained frameworks for standard work. Custom trainers, evaluators,
  runtimes, kernels, cache policies, or distributed mechanisms are allowed when
  they are part of or necessary for the contribution; justify and validate them
  against a trusted reference.
- Long jobs may use the supervised subagent system, a scheduler, or the
  project's native runner. Parallelism is optional and bounded by real
  resources.

## Pipeline state contract

`research/PIPELINE_STATE.json` is the mission ledger. The Engineer may update
descriptive fields such as objective, target venue, and artifact paths. Stage
fields (`current_stage` and per-stage statuses) are Manager-owned.

The canonical stage order is:

```text
research → plan → benchmark → run → analysis → draft → review → submission
```

## Artifact consistency

From analysis onward:

- keep canonical raw evidence separate from generated reports;
- generate tables, figures, and manuscript numbers from canonical sources;
- maintain `paper/ARTIFACT_MANIFEST.json` with paths, hashes, schemas, and source
  links;
- refresh downstream artifacts after source changes;
- never hand-edit generated review artifacts or success labels;
- keep exact local commands and paths in manifests/logs rather than rendered
  manuscript prose.

## Final research-paper contract

A project may finish with a positive, negative, diagnostic, characterization,
or boundary contribution when:

- the research question is important and literature-grounded;
- the result is falsifiable and supported by authentic evidence;
- empirical claims include appropriate public benchmark/data evidence;
- the strongest relevant comparisons and confounds are handled fairly;
- uncertainty and repeatability are appropriate to the data-generating process;
- claims are scoped to what was actually measured or proved;
- the bibliography contains at least 35 verified BibTeX entries and the paper
  source cites at least 30 unique keys;
- the paper follows the selected venue's current official template and rules;
- citations, figures, tables, reviews, and submission artifacts are current;
- the L2 Reviewer certifies the full pipeline checklist.

A method losing to a baseline is not an automatic pivot. If the run is valid and
the loss, null, or boundary changes understanding or practice, preserve it and
write the strongest honest paper supported by the evidence.

## Non-data figure contract (retain as a hard requirement)

- Data, metric, and result plots may be generated from scripts.
- Every other paper-facing figure — Figure 1, teaser, overall,
  method/framework/system/pipeline overview, schematic, qualitative/example
  visual, architecture diagram, or explanatory non-data figure — must use the
  actual image-2/codex-image2 raster output in `paper/main.tex`.
- The Engineer must not draw, redraw, trace, clean, or improve a non-data figure
  with matplotlib/FancyBboxPatch, TikZ node graphs, SVG/PIL/HTML canvas,
  Inkscape/manual vector tools, screenshots, or cleaned PDF derivatives.
- Record prompt path, output path, dimensions, SHA-256, raw generation sidecar,
  inspect sidecar, generation provenance, and model-backed review path in
  `paper/figures/IMAGE2_FIGURES.json`.
- The prompt must be created with
  `python -m argus_skill.tools.image_tool paper-prompt ...` and retain
  `argus-image2-paper-prompt-v1` plus
  `paper-framework-figure-studio-pro-v3.1.4a`.
- Generate and review 6–20 Figma-style layout variants by changing only the
  named layout/candidate-contract fields. Keep the strongest reviewed,
  page-readable raster.
- Preserve the exact accepted raster bytes after provenance is recorded. If the
  figure is weak, regenerate through image-2; do not repair only metadata or
  substitute a locally drawn asset.

## Stage guidance

### 1. Research

- Build `research/LITERATURE_GROUNDING.json` around claim coverage: nearest
  competitors, foundations, contradictions, negative evidence, and open
  frontier.
- Write `research/RESEARCH_BRIEF.md`, `research/IDEA_REJECTION_LOG.md`, and
  `research/GO_NO_GO.md`.
- Run the cheapest faithful falsification or characterization probe of the
  binding premise.
- Diagnostic and negative-result directions are allowed when they answer an
  important question.

### 2. Plan

- Write `research/EXPERIMENT_PLAN.md`.
- Define hypotheses/questions, strongest relevant comparisons, public evidence
  sources, metrics or proof obligations, controls/ablations, uncertainty method,
  budget, and stopping criteria.
- Select infrastructure after the idea survives de-risk.
- Do not impose fixed benchmark, baseline, task, or duration counts.

### 3. Benchmark

- Select and prepare appropriate public benchmarks/data/task suites.
- Record official source, version, split, license/access, evaluation unit,
  metric/evaluator, filtering, and claim tested.
- Synthetic diagnostics remain separate and supplementary.
- Run a faithful smoke test through the real evaluator or analysis path.

### 4. Run

- Execute via `Research Experiment Runner`.
- Preserve manifests, raw evidence, logs, status/progress for long jobs, and
  cancellation state.
- Run every claim-relevant condition or record an evidence-backed exclusion.
- Classify outcomes as supported positive, supported negative, supported
  boundary, misconfigured, inconclusive, or infeasible under budget.

### 5. Analysis

- Regenerate all aggregates from raw artifacts.
- Map claims to evidence.
- Keep losing, null, and contradictory comparisons visible.
- Produce data figures/tables plus the required image-2 non-data figures.

### 6. Draft

- Use the selected `research/VENUE_PROFILE.json` and official author kit.
- Write the paper around the supported research value, whether positive,
  negative, diagnostic, or boundary.
- Do not pad to a historical EMNLP/AAAI shape.

### 7. Review

- Run venue-aware academic-language, infrastructure-leak, layout, citation, and
  claim-evidence reviews.
- Fix source artifacts and rerun the owning review; never hand-edit PASS state.

### 8. Submission

- Build `paper/SUBMISSION_ASSURANCE.json`.
- Verify the selected venue deadline/profile is current and the package obeys
  its official rules.
- Require full-pipeline L2 Reviewer certification before declaring completion.

## Response shape

- State current stage and the strongest supported research conclusion.
- Name changed artifacts and decisive evidence.
- Report positive, negative, and inconclusive findings without spin.
- If blocked, state the exact missing external condition or evidence.
