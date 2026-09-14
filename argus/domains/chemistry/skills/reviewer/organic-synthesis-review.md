---
name: "Organic Synthesis and Retrosynthesis Review"
description: "Independently review small-molecule reaction records, retrosynthetic routes, and route-validation claims for identity, precedent, selectivity, feasibility, analytical evidence, safety, and honest claim limits."
---

## When to use

Use to review organic reaction extraction, retrosynthetic analysis, route
ranking, forward-prediction support, or experimental route-validation results.

## Do not use when

Do not apply this rubric to metabolic pathway engineering, biomolecular assays,
materials processing, MOF assembly, or crystal refinement.

## Review procedure

1. Reconstruct target, intermediates, stereochemistry, salts/solvates, and
   component roles from original sources.
2. Check whether each step has applicable precedent rather than only a similar
   reaction name or model score.
3. Audit chemo-, regio-, stereo-, and functional-group compatibility across the
   route, plus protecting-group and redox burden.
4. Verify that availability, yield, selectivity, scale, workup, purification,
   and safety claims use source evidence with matching conditions.
5. Distinguish route generation, forward prediction, precedent, diagnostic
   experiment, and isolated physical result.
6. Inspect raw analytical evidence and controls when experimental success is
   claimed.
7. Compare alternatives under the same target form and route objectives.

## Rejection conditions

Return `replan_requested` when a key step lacks applicable precedent or a
credible de-risking experiment, identity or stereochemistry is unresolved,
route ranking hides hard constraints, a model output is presented as synthetic
validation, or physical execution lacks authorization and safety controls.

## Done standard

Return `done` only at the requested evidence level. State whether the result is a
well-supported conceptual route, a precedent-backed execution candidate, or an
experimentally demonstrated sequence. Do not certify full-route feasibility
from isolated validation of one step.
