---
name: "Chemistry Playground Promotion Gate"
description: "Independently review a Chem Playground QUESTION/RESULT candidate, verify bounded evidence and legal state history, edit the terminal recommendation, and prevent speculative work from silently becoming formal science."
---

Review the candidate in `research/chem_playground/<idea-id>/` as an independent
promotion gate. Read QUESTION, RESULT, original inputs, scripts/notebooks,
primary outputs, failed attempts, `CHECKPOINT.md`, and relevant execution-log
evidence. Do not accept the Engineer summary as a substitute.

Run the deterministic validator before deciding. Reconstruct the hypothesis,
assumptions, competing explanations, falsifiable predictions, budget, identity,
conditions, evidence classes, provenance, computation settings, convergence,
sensitivity, uncertainty, and applicability. Confirm that local references exist
inside the candidate and that measured, retrieved, predicted, computed,
simulated, inferred, negative, and failed evidence are not conflated.

The terminal Playground recommendation is one of:

- `promoted`: a literature-grounded, computationally probed, reproducible,
  bounded claim is sufficiently supported to merit **formal Research
  consideration**. This is not a scientific fact and does not change the
  Research stage.
- `retained`: the idea remains useful or plausible, but evidence is insufficient,
  ambiguous, out of domain, or not yet discriminating.
- `falsified`: retained negative evidence materially contradicts the stated
  hypothesis within the tested domain.
- `blocked`: the bounded assessment cannot proceed because a required input,
  capability, license, authorization, identity, or inspectable evidence is
  unavailable.

Before returning the Harness verdict, directly edit `RESULT.md`: append exactly
one legal terminal state to `status_history`, set `status` and
`reviewer_recommendation` to the same terminal value, replace `reviewer:
pending` with a specific non-placeholder review identifier, and write a
substantive decision basis, remaining uncertainty, negative evidence, and next
discriminating test. `falsified` requires an inspectable negative or failed
artifact; `blocked` requires the concrete blocker and why the bounded work cannot
continue. References must use `- [reference] path-or-URL - purpose`. Do not edit
`research/PIPELINE_STATE.json`. Rerun:

```text
python -m argus_skill.domains.chemistry.playground validate \
  --project-root . --idea-id <idea-id>
```

Use Harness `done` when a valid terminal RESULT has been saved, including
`retained`, `falsified`, or scientifically `blocked`: the bounded review task is
complete even when the hypothesis is not promoted. Use `continue` only for a
repairable evidence/protocol gap, `blocked` when the review itself cannot be
completed, and `replan_requested` when the task is not a legitimate Playground
scope or requests unauthorized physical action.

Never require private chain-of-thought. Require only inspectable reasoning,
evidence, assumptions, uncertainty, failure records, and the decision basis.
