---
name: "Auto Research Pipeline"
description: "PRIMARY ENTRY POINT for full AI research projects across methods, systems, theory, interpretability, evaluation, data, diagnostics, and positive/negative/boundary findings. Orchestrates literature → plan → public evidence → execution → analysis → venue-aware paper → review → submission."
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
- a rigorous negative or boundary result only when it yields a surprising,
  robust, independently useful insight rather than merely documenting failure;
- a reproducible systems or efficiency finding.

The acceptance question is whether the work has a strong thesis and evidence
appropriate to it. Positive metrics are not mandatory, but a completed
experiment or honest failure report is not automatically a paper.

## Non-negotiable research bar

- Publishable/doctoral work requires a nontrivial technical core, verified
  originality, claim-relevant formal/causal grounding, and field-level
  consequence. Feasibility cannot rescue prompt/schema/wrapper/scale variants or
  decorative mathematics.
- Ground the question in primary literature, official artifacts, and the
  strongest relevant prior work.
- State one clear thesis or research question and the result that would support,
  weaken, or refute it.
- Preserve negative, contradictory, and failed evidence in the internal audit
  trail. The manuscript selectively presents the strongest valid evidence for its
  thesis plus claim-critical contrary evidence; it is not an experiment diary.
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
- A repeat detects sampling noise and nothing else. Name that failure mode before
  buying N repeats; if the margin is far from the noise floor, one run suffices.
  Repeats cannot detect wrong implementation, a broken control, or claim-code
  mismatch, so the claim-to-code faithfulness trace and positive control come
  first. Preserve repeats when the margin is near the noise; flag unchanged-
  configuration reruns motivated only by generalized fear as ritual waste.
- Do not invent project-specific round-number improvement or error thresholds
  merely to obtain a binary keep/reject rule. A numeric cutoff needs a basis in
  utility, risk, domain standards, prior evidence, theory, or prospective
  sensitivity. Otherwise report the effect, uncertainty, regimes, and
  cost-quality frontier continuously; a modest credible effect is a reason to
  push the method further, and only a claim no affordable experiment can reach
  gets narrowed.

## Venue selection

If the operator explicitly names a venue, use that venue and verify the current
cycle and official author kit.

If the venue is unspecified:

1. Do not infer, search for, or select a venue.
2. Keep venue-dependent draft/review/submission work blocked.
3. Ask the operator to name a venue or explicitly request venue discovery.

Only when the operator explicitly requests venue discovery may the task compare
candidates using current primary sources. Do not restrict that comparison to any
ranking or classification system unless the operator requested it. Never
silently default to a conference.

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

`.argus/PIPELINE_STATE.json` is the mission ledger. The Engineer may update
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

A project may finish with a paper only when:

- the research question is important and literature-grounded;
- the result is falsifiable and supported by authentic evidence;
- empirical claims include appropriate public benchmark/data evidence;
- the strongest relevant comparisons and confounds are handled fairly;
- uncertainty and repeatability are appropriate to the data-generating process;
- every repeat answers a named sampling-noise question after method faithfulness
  and positive-control checks, rather than serving as generalized assurance;
- claims are scoped to what was actually measured or proved;
- the paper has one coherent, venue-relevant thesis and a clear strongest accept
  argument;
- a method paper's implementation has received a fair, competitive engineering
  effort before scientific failure is inferred;
- negative or boundary evidence is the paper's contribution only when its
  insight stands independently of the failed implementation;
- bibliography coverage is claim-complete and verified, without a universal
  entry count;
- the paper follows the selected venue's current official template and rules;
- citations, figures, tables, reviews, and submission artifacts are current;
- the L2 Reviewer certifies the full pipeline checklist.

A method losing to a baseline triggers implementation and research diagnosis,
not automatic write-up. Preserve the result and enter a positive-recovery loop:
debug implementation, optimization, data, evaluator, scale, and the method itself;
reproduce reference behavior; then rerun decisive tests. Aim to recover a genuine
positive result with evidence proportional to the claim and available budget. There
is no universal requirement that every seed, benchmark, or strongest baseline must
succeed. Protocols may change for a documented scientific reason when prior outcomes
remain visible and the final claim is scoped accordingly. Draft after Reviewer
certifies engineering adequacy and an independently defensible publication thesis.

After an idea is selected, do not use a weak first result as an excuse to write a
negative paper. Give the core mechanism a faithful, competitive implementation;
diagnose concrete failures; make targeted improvements; and rerun decisive tests
while credible fixes remain.

## Research figure contract

- Use the research vertical's Research Visualization Router for every figure.
- Renderer choice belongs to the Engineer and Reviewer, grounded in the figure
  brief, scientific semantics, editability, and actually available capabilities.
- Optionally record source/renderer handoff metadata in
  `paper/figures/FIGURE_PROVENANCE.json`; it is not a completion gate.
- Image-2 is one optional renderer. When selected, retain its canonical prompt,
  raw sidecars, accepted raster, review, and `IMAGE2_FIGURES.json`; when
  unavailable, use a truthful deterministic route rather than fabricating
  image-2 metadata or blocking the paper solely on the API.
- Reviewer uses a good-enough visual standard. Do not repeat figure regeneration
  for minor aesthetic preferences once the figure is readable, coherent, and
  attractive enough.
- When the router selects image-2, generate/review its required candidate set,
  register through `sync-paper-metadata`, reuse a valid frozen cache, and preserve
  exact accepted raster bytes. These image-2-specific rules do not apply to
  browser-rendered HTML/diagram/PPT renderers, which repair their own source and
  re-render.

## Stage guidance

### 1. Research

- Build `research/LITERATURE_GROUNDING.json` around claim coverage: nearest
  competitors, foundations, contradictions, negative evidence, and open
  frontier. Keep visible AI-venue/recent-arXiv and foundation-theory coverage
  notes. Imbalance is advisory, not a fixed quota or completion gate; explain
  missing coverage and proceed.
- Write `research/RESEARCH_BRIEF.md` and preserve rejected ideas with their real
  observations in the existing project history.
- Do not lock an idea until the ambition standard survives independent
  prior-art attack and adversarial review.
- Stream discovery into validation: each completed route receives a fresh
  independent review immediately. A fresh selector judges when genuinely distinct
  mechanism families cover the alternatives and key uncertainties well enough to
  choose. It reads all evidence that has arrived, including probes and later routes,
  and may update the choice when new evidence changes credibility. Judge important,
  credible, nontrivial knowledge in any contribution form; local convenience is not
  scientific value, and resource gaps belong in the staged plan.
- Search mathematical, physical, statistical, or other foundations when they can
  bear load for the claim. Prefer distinct relevant lenses over variants of one
  analogy, and explain what each lens lets the work establish.
- Reuse the active independent route pipeline; do not start another breadth
  sweep under new route names.
- Validate each finalist in one decision-sized milestone, but preserve the
  dependency inside that milestone: first complete the nearest-source grounding,
  prior-art attack, technical/formal validity check, and independent selection
  decision; only a selected survivor may proceed to probe design and execution.
  A rejected or still-unresolved idea must not consume model, API, or GPU calls.
  Run independent finalists concurrently, while keeping selection-before-probe
  ordering within each finalist. Keep probes below ten minutes by default and
  never turn them into formal benchmarks, training, or broad sweeps. Do not
  serialize "repair research canon,"
  "build smoke harness," and "judge smoke" into separate Planner missions when
  one coherent milestone can own the ordered conditional branch.
- Treat the probe as an observation, not a keep/reject gate. A ceilinged,
  floored, too-easy, underpowered, or poorly implemented result is merely a
  limitation note; record missing headroom explicitly. Never reject a
  qualitatively strong idea from a research smoke probe; later stages own
  faithful benchmarking and iterative refinement.
- Before paid/model-backed execution, verify that candidate predictions cannot
  read gold labels or scorer-derived fields and that baselines receive the same
  decision-time information.
- Store measurements separately from the routing verdict. A fresh Reviewer
  authors `advance`, `reject`, or `inconclusive`; the harness never derives that
  verdict mechanically from a metric threshold.

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
- Underperformance must receive an implementation-adequacy audit and credible
  targeted optimization when justified; no fixed retry count substitutes for
  scientific judgment.

### 5. Analysis

- Regenerate all aggregates from raw artifacts.
- Map claims to evidence.
- Keep all valid losing, null, and contradictory comparisons in the evidence
  record. In the paper, include those that materially bear on the thesis; keep
  secondary dead ends in audit artifacts or an appendix instead of dumping every
  run into the main story.
- Select one defensible thesis before drafting. If no strong thesis survives,
  return to implementation, experiments, or research/plan as the diagnosed cause
  requires.
- Produce the required data and conceptual figures through the Research
  Visualization Router; image-2 is conditional on capability and renderer choice.

### 6. Draft

- Use the selected `research/VENUE_PROFILE.json` and official author kit.
- Write the paper as an argument for one supported insight, not a report of what
  experiments happened. Select the most probative valid evidence, organize it by
  the questions needed to establish the thesis, and omit nonessential run
  chronology. Do not introduce a method as the contribution and then center the
  manuscript on why it failed.
- Do not pad to a historical EMNLP/AAAI shape.

### 7. Review

- Run venue-aware academic-language, infrastructure-leak, layout, citation, and
  claim-evidence reviews.
- Fix source artifacts and rerun the owning review; never hand-edit PASS state.

### 8. Submission

- Verify the selected venue deadline/profile is current and the package obeys
  its official rules.
- Ask the L2 Reviewer to read the current paper and claim-critical sources
  directly before declaring completion. Do not build an assurance packet.

## Response shape

- State current stage and the strongest supported research conclusion.
- Name changed artifacts and decisive evidence.
- Report positive, negative, and inconclusive findings without spin.
- If blocked, state the exact missing external condition or evidence.
