---
name: "Computational Chemistry Convergence and Interpretation Validation"
description: "Validate numerical convergence, model sensitivity, and claim boundaries for molecular or periodic calculations; excludes general materials characterization, battery degradation diagnosis, and biological activity claims."
---

## When to use
Use after a computational chemistry workflow yields a result intended for comparison,
ranking, mechanism, or decision support.

## Do not use when
Do not use as a generic software test or to certify experimental truth from a computed
number.

## Scientific question
Is the conclusion stable to numerical settings and credible model alternatives at the
resolution claimed?

## Required inputs
Primary outputs, exact inputs, convergence history, method rationale, alternative
states/models, comparison data and their provenance, and the proposed claim.

## Identity and normalization
Ensure all comparisons use the same species/state, stoichiometry, reference state,
units, temperature convention, and energy zero. Mark literature values as retrieved,
calculations as computed/simulated, and experiments as measured.

## Decision procedure
Test the most claim-sensitive numerical dimensions first: basis/cutoff and k-points,
SCF thresholds, cell size, integration grid, timestep, replica count, and sampling
length. Then test credible method alternatives and state identities. Compare only
like observables; diagnose disagreement before averaging it away.

## Tool-selection ladder
Use native output and independent parsers first, simple plots/statistics next, then
alternate engines or reference methods when the result merits them.

## Minimum capability probe
Reproduce one reported value from preserved inputs and independently recompute its
units/reference conversion.

## Evidence to retain
Parameter sweeps, raw outputs, analysis scripts, comparison tables, failed or
divergent alternatives, and the final claim-to-evidence mapping.

## Validation gates
Numerical tolerance must be smaller than the claimed effect; sampling diagnostics must
support effective sample size; method/state sensitivity must not reverse the claim, or
the claim must be narrowed.

## Common failure modes
Confusing precision with accuracy, comparing different protonation states, hidden
reference-energy changes, discarding unstable trajectories, and selecting only the
best agreement with experiment.

## Uncertainty and applicability domain
State numerical, statistical, and model-form uncertainty separately. Extrapolation to
unmodeled phases, compositions, timescales, or reactivity remains unsupported.

## Safety and authorization
Do not alter raw outputs to meet a target. Respect authorization for external
reference datasets.

## Output contract
Provide a verdict of supported, sensitivity-limited, or unsupported; retain negative
tests and give an evidence ceiling appropriate to computed/simulated evidence.

## Stop, block, or replan conditions
Replan if the effect is smaller than uncertainty, alternatives reverse the conclusion,
or missing experimental information makes comparison non-equivalent.

## Official references
- [NIST CCCBDB](https://cccbdb.nist.gov/)
