---
name: "Research Brief To Experiment Plan"
description: "Turn a research seed into a literature-grounded thesis, implementation strategy, and claim-driven experiment plan."
---

# Research Brief to Experiment Plan

## Goal

Choose a research direction worth engineering well. The output is not a list of
artifacts or an experiment matrix for its own sake; it is a defensible thesis and
the cheapest credible route to determine whether that thesis can become a strong
paper.

For publishable/doctoral work, require a nontrivial technical core, verified
originality, claim-relevant formal/causal grounding, and field-level
consequence. Feasibility cannot rescue a shallow prompt/schema/wrapper/scale
variant or decorative mathematics.

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
- What general belief, design principle, or capability changes if the thesis is
  true?

Record rejected alternatives only when they affected the decision; do not create
a rejection quota.

Do not design or execute an experiment until this method-reasonableness case has
been completed and the candidate has been selected.

## 3. Freeze the hypothesis-to-implementation contract

After selection and before writing experimental code, use
`engineer/hypothesis-implementation-contract.md` to write
`research/HYPOTHESIS_IMPLEMENTATION_CONTRACT.md`. Bind every load-bearing
hypothesis element to the planned implementation, baseline, control, observable,
and invariant. Then implement the selected idea and have a fresh Reviewer compare
the actual entry point and call chain with the frozen contract before any
claim-bearing execution.

Do not insert a tiny scientific probe between selection and the real experiment.
Unit and smoke checks may establish only wiring, shapes, and external
availability after implementation; their outcomes cannot re-rank the idea or
stand in for scientific evidence.

## 4. Design the implementation to give the idea a fair chance

Study the strongest relevant implementation and reuse maintained infrastructure
when it is not the contribution. The plan should name:

- what is reused and what must be new;
- how each mechanism and formula maps to the actual entry point and code path;
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
- relevant competitive baselines and claim-critical controls/ablations, proportional
  to the claim and available budget;
- fair budgets/configurations and implementation validation;
- uncertainty/repeatability appropriate to the data;
- staged execution from real smoke to decisive evidence;
- observability/cancellation for long work;
- success, failure, and pivot criteria.

Scale follows the claim. Do not impose universal benchmark, task, model, seed,
duration, or effect-size counts. Every empirical paper claim needs authentic
public evidence; synthetic diagnostics may supplement but not replace it.

A numeric success/failure cutoff needs a defensible external basis: user
utility or risk, an accepted domain standard, prior evidence, a theoretical
prediction, or a prospective power/sensitivity target. Preregistration alone
does not justify an unsupported round-number improvement or error cap. When no
such cutoff exists, predeclare the estimand, expected direction, matched
budgets, uncertainty analysis, and effect-cost tradeoff; interpret the
continuous evidence rather than turning it into an automatic keep/kill gate.
Missing an arbitrary target is not evidence that an idea failed.

## 6. Advise the Planner

End the research brief with the current scientific case for the thesis, the
strongest concern, and the observations that would most change the plan. Do not
write a separate binary verdict file or turn a local probe into an automatic
pivot. The Planner reads the stored evidence and decides the next direction.

## Minimal artifact set

Use existing canonical artifacts whenever possible:

- `research/RESEARCH_BRIEF.md`;
- `research/LITERATURE_GROUNDING.json`;
- `research/HYPOTHESIS_IMPLEMENTATION_CONTRACT.md`;
- `research/EXPERIMENT_PLAN.md`;
- benchmark/data provenance and code-reuse notes when applicable.

Do not create duplicate JSON/Markdown mirrors, fixed-length style reports, or
checklist artifacts that add no new scientific information.

## Response shape

State the thesis, why it matters, the decisive next experiment, the strongest
baseline, and the main engineering risk.
