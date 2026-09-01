---
name: "Idea Creator"
description: "Review a broad idea portfolio, use relevant evidence, and select the strongest credible research contribution."
---

# Idea Creator — review evidence, then commit

`idea-discovery` streams independent mechanism families. Review each route as it
arrives, then let a fresh selector decide when the portfolio covers the important
alternatives and uncertainties well enough to choose. A twelve-route fanout is a
useful operating example for some broad problems, not a quota or proof of breadth.

## Review each route

Independently check the claim-critical frontier and strongest prior-art attack.
Judge whether the route could produce important, credible, nontrivial new knowledge.
That knowledge may be a theory result, measurement, dataset, method, anomaly,
negative result, or boundary condition; no contribution category receives automatic
preference. Missing implementation detail is uncertainty to record, not by itself a
reason to reject an otherwise strong research case.

Do not reward a route for being cheap, training-free, locally convenient, or quick to
verify. Treat feasibility as a staged resource plan. Summarize the contribution,
evidence, fatal concerns, and future decisive experiment in natural language rather than
manufacturing scores.

## Select before experiments

Idea selection uses primary literature, official source inspection, explicit mechanism
reasoning, strongest-reduction analysis, and a credible full-scale evidence plan. Do
not execute candidate code or run toy, premise, feasibility, smoke, or other probe
experiments while generating, reviewing, or selecting ideas. Describe the experiment
that would later test the idea, but do not create result evidence now. If a legacy
pre-selection probe exists, record it as a protocol deviation and do not use its
outcome to rank candidates.

## Make the adversarial selection

The fresh selector reads every relevant route, review, and late non-experimental
selection artifact that has arrived. It first asks whether the portfolio contains genuinely different mechanism
families and covers the uncertainties that could change the decision. If not, it asks
for the missing kind of evidence rather than satisfying a route count.

When coverage is sufficient, choose the qualified route with the strongest supported
research value and write the task-owned
`research/ideation/portfolios/<direction>/selection.json`. The validated
portfolio workflow materializes `research/IDEA_SELECTION.json`; the selector
must not write that shared path directly. Record the evidence considered, the
reason this route survives the alternatives, resource needs, and unresolved
risks. The selection record is required; its prose and contribution shape are
not templated.

## Keep the portfolio honest

- Preserve rejected routes and the evidence that defeated them.
- Let credible later evidence reopen the comparison.
- Keep idea selection free of experimental outcomes.
- Do not substitute local convenience for contribution quality.
- Do not start a duplicate portfolio under new route names.

The selected route proceeds into the existing research brief and experiment-planning
artifacts. The plan stage freezes
`research/HYPOTHESIS_IMPLEMENTATION_CONTRACT.md` before experimental code or
result-producing execution.
