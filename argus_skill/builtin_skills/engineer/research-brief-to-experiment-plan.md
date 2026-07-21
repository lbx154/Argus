---
name: Research Brief To Experiment Plan
description: Turn a research seed into a literature-grounded thesis, implementation strategy, and claim-driven experiment plan.
category: research-planning
version: 2
created_at: 2026-07-20T00:00:00+00:00
---

# Research Brief to Experiment Plan

## Goal

Choose a research direction worth engineering well. The output is not a list of
artifacts or an experiment matrix for its own sake; it is a defensible thesis and
the cheapest credible route to determine whether that thesis can become a strong
paper.

## 1. Ground the problem

Use primary literature, official benchmarks/data, and relevant released code.
Identify:

- the important unsolved pain point;
- the nearest competing explanations or methods;
- the strongest feasible baseline;
- the exact gap left open;
- why resolving the gap would matter to the target community.

Maintain one canonical literature ledger and a concise synthesis in
`research/RESEARCH_BRIEF.md`. Coverage follows the claims; there is no paper,
query, citation, or repository quota. Clone and inspect code only when it will be
reused, reproduced, or materially informs implementation.

Select the venue from the operator's request or current primary-source venue
information. Do not silently default to EMNLP or AAAI.

## 2. Form a thesis, not an activity

The candidate idea must have one non-trivial insight:

> Under setting Y, mechanism X should resolve problem P because W.

Reject directions whose contribution is only "apply A to B," whose gap is
manufactured, or whose outcome would be uninteresting either way. For each
serious candidate ask:

- What would make a skeptical reviewer care?
- What observation would falsify the binding premise?
- What alternative explanation must the design distinguish?
- What engineering capability must exist for the idea to receive a fair test?

Record rejected alternatives only when they affected the decision; do not create
a rejection quota.

## 3. De-risk the binding premise

Run the cheapest faithful real probe that can invalidate the central assumption.
A smoke test proves wiring, not the idea. If the premise fails, decide whether
the failure comes from:

- implementation/configuration/evaluator error;
- an underpowered or under-optimized realization;
- an unfair comparison;
- a genuine scientific limitation;
- insufficient resources for a fair test.

Do not treat passing unit tests as implementation correctness. Compare against a
trusted reference, inspect executed behavior, and measure the quantity the
mechanism is supposed to change.

## 4. Design the implementation to give the idea a fair chance

Study the strongest relevant implementation and reuse maintained infrastructure
when it is not the contribution. The plan should name:

- what is reused and what must be new;
- how proposed and baseline paths remain comparable;
- reference behavior that validates the implementation;
- likely optimization/tuning bottlenecks;
- diagnostics that distinguish engineering failure from method failure.

Method-specific details belong in the matched skill or Planner-authored project
checklist, not a universal research form. RL, systems, theory, clinical, and
evaluation projects should not fill one another's schemas.

## 5. Write the claim-driven experiment plan

`research/EXPERIMENT_PLAN.md` should contain only what execution needs:

- thesis and claim(s) under test;
- public evidence source and authentic evaluator;
- strongest baseline and claim-critical controls/ablations;
- fair budgets/configurations and implementation validation;
- uncertainty/repeatability appropriate to the data;
- staged execution from real smoke to decisive evidence;
- observability/cancellation for long work;
- success, failure, and pivot criteria.

Scale follows the claim. Do not impose universal benchmark, task, model, seed,
duration, or effect-size counts. Every empirical paper claim needs authentic
public evidence; synthetic diagnostics may supplement but not replace it.

## 6. Advise the Planner

End the research brief with the current scientific case for the thesis, the
strongest concern, and the observations that would most change the plan. Do not
write a separate binary verdict file or turn a local probe into an automatic
pivot. The Planner reads the stored evidence and decides the next direction.

## Minimal artifact set

Use existing canonical artifacts whenever possible:

- `research/RESEARCH_BRIEF.md`;
- `research/LITERATURE_GROUNDING.json`;
- `research/EXPERIMENT_PLAN.md`;
- benchmark/data provenance and code-reuse notes when applicable.

Do not create duplicate JSON/Markdown mirrors, fixed-length style reports, or
checklist artifacts that add no new scientific information.

## Response shape

State the thesis, why it matters, the decisive next experiment, the strongest
baseline, and the main engineering risk.
