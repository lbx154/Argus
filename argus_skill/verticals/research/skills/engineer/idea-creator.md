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
evidence, fatal concerns, and useful probe evidence in natural language rather than
manufacturing scores.

## Use probes as evidence

Run a bounded feasibility observation only when it can clarify plumbing, data shape,
evaluator availability, or another decision-relevant uncertainty without pretending
to be the full experiment. Otherwise leave the scientific question for the stage that
can test it properly. Preserve weak, null, contradictory, and negative observations;
when new evidence materially changes a route's credibility, update the judgment and
explain why. When a probe exists, keep its evidence in the existing
`research/ideas/<id>/EVIDENCE.json` record.

## Make the adversarial selection

The fresh selector reads every relevant route, review, probe, and late result that has
arrived. It first asks whether the portfolio contains genuinely different mechanism
families and covers the uncertainties that could change the decision. If not, it asks
for the missing kind of evidence rather than satisfying a route count.

When coverage is sufficient, choose the qualified route with the strongest supported
research value and write `research/IDEA_SELECTION.json`. Record the evidence considered,
the reason this route survives the alternatives, resource needs, and unresolved risks.
The selection record is required; its prose and contribution shape are not templated.

## Keep the portfolio honest

- Preserve rejected routes and the evidence that defeated them.
- Let credible later evidence reopen the comparison.
- Do not turn a feasibility check into a full benchmark or training campaign.
- Do not substitute local convenience for contribution quality.
- Do not start a duplicate portfolio under new route names.

The selected route proceeds into the existing research brief and experiment-planning
artifacts. Later stages choose evidence strictness in proportion to the claims made.
