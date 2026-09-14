---
name: "Chemistry Research Review"
description: "Provide cross-domain chemistry review and route to specialized reviewer Skills while enforcing identity, primary evidence, controls, uncertainty, reproducibility, safety, and claim ceilings."
---

Review the scientific result, not paperwork. Read the original question,
original inputs, source data, code or configuration, primary tool/instrument
outputs, controls, negative results, and the exact claim. Missing process-only
manifests are not defects; their presence is not evidence.

Use the specialized reviewer Skill for the task:

- organic synthesis;
- materials science;
- crystallography;
- MOF and reticular chemistry;
- computational chemistry;
- batteries and electrochemistry;
- characterization;
- biochemistry and chemical biology.

For an explicitly matched Chem Playground candidate, use the Chemistry
Playground Promotion Gate. A valid `promoted`, `retained`, `falsified`, or
scientifically `blocked` RESULT may complete the bounded mission with Harness
`done`; none of those statuses changes the formal Research stage.

For cross-domain claims, apply each relevant standard without double-counting
the same evidence. Reconstruct chemical/sample/construct identity, units,
conditions, transformations, software or instrument state, calibration,
convergence, controls, grouping, uncertainty, and applicability.

Distinguish retrieved, curated, predicted, computed, simulated, measured, and
inferred results. Enforce the claim ceiling: a route planner does not prove
synthesis; a valid CIF does not prove a new physical sample; an ideal structure
does not prove porosity; a docking pose does not prove binding; a model fit does
not prove mechanism; a clean exit does not prove correctness.

For discovery or optimization, inspect source provenance, licenses, duplicates,
split/group leakage, information available at each decision, baselines under the
same budget, calibration, and the full trajectory including failures. When
online agent control is claimed, verify that decisions were actually made online
rather than by a policy frozen before outcomes.

Physical evidence must trace to an identified sample and an authorized facility
or instrument path. Planning, computation, or simulation cannot be certified as
physical execution.

Return `done` only at the requested evidence level and state what was retrieved,
predicted, computed, simulated, measured, inferred, reproduced, improved,
falsified, or unresolved. Return `replan_requested` when identity, primary
evidence, controls, capability, uncertainty, applicability, or authorization
cannot support a valuable decision. A bounded negative result may complete its
experiment but does not automatically complete the research objective.
