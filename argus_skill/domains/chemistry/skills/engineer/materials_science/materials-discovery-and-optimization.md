---
name: Materials Discovery and Optimization Workflow
description: Execute a bounded materials discovery, screening, or processing optimization study with explicit design space, property objectives, uncertainty, baselines, and validation; excludes crystal refinement, MOF topology, and battery cell cycling.
category: chemistry-materials-discovery
version: 1
---

## When to use

Use for composition or process screening, multi-objective candidate ranking,
active learning, surrogate-assisted optimization, or hypothesis generation for
non-MOF materials and material systems.

## Do not use when

Do not use for solving a diffraction structure, assigning MOF nets/linkers, or
analyzing electrochemical cell degradation. Do not claim discovery from
rediscovering database entries or optimizing a hidden benchmark.

## Scientific question

Define the material class, controllable variables, target properties, hard
constraints, operating conditions, evidence level, budget, and whether success
means a predicted candidate, computed validation, synthesized sample, or
measured property.

## Required inputs

Design-space definition; material/process identity rules; source data and
licenses; property definitions; feasibility and safety constraints; available
calculation/measurement oracles; baseline methods; budget; and holdout policy.

## Decision procedure

1. Define candidate representation without excluding plausible chemistry or
   admitting impossible compositions/processes.
2. Audit source data coverage, bias, duplicate families, and leakage.
3. Establish simple and strong baselines before adaptive search.
4. Choose physics-, chemistry-, or data-driven models fit to the data volume and
   decision.
5. Quantify uncertainty and identify out-of-domain candidates.
6. Select candidates by declared multi-objective and constraint logic; retain the
   complete proposal/observation trajectory.
7. Validate top candidates with an evidence-producing method independent enough
   to test the proposal.
8. Compare methods under the same information and evaluation budget.

## Tool-selection ladder

Use curated experimental/computational data and project-native models first;
query materials databases through documented APIs; use established simulation
or workflow engines for claim-critical calculations; use active learning only
when observations genuinely update later proposals. A language model may frame
hypotheses but not fabricate property values.

## Minimum capability probe

Run one known material through representation, model/oracle, constraints,
uncertainty, and output parsing. Verify unit and property definitions and confirm
the evaluation target is unavailable to proposal logic.

## Evidence to retain

Keep design space, data snapshot, splits, candidate identities, model/version,
features, seeds, proposal order, oracle inputs/outputs, failed candidates,
constraint violations, baselines, and uncertainty.

## Validation gates

- Hold out related composition/prototype/process families as required by the
  intended generalization claim.
- Validate feasibility constraints independently of the optimization score.
- A high predicted score requires uncertainty and domain checks.
- A computed candidate is not synthesized; a synthesized candidate is not a
  measured property; one measured property does not establish generality.
- Report negative and infeasible candidates, not only the winner.

## Uncertainty and applicability domain

State interpolation versus extrapolation, data coverage, model calibration,
property tradeoffs, and sensitivity to representation and constraints.

## Safety and authorization

Candidate generation does not authorize synthesis, scale-up, procurement, or
device operation. Screen hazards and facility constraints before physical work.

## Output contract

Return problem definition, candidate space, methods, baselines, validation
budget, ranked candidates with uncertainty and feasibility, retained negative
results, and the evidence level of each conclusion.

## Stop, block, or replan conditions

Replan when the oracle does not measure the requested property, leakage prevents
honest evaluation, uncertainty overwhelms ranking, or no candidate satisfies
hard feasibility and safety constraints.

## Official references

- Materials Project API: https://materialsproject.org/api
- OPTIMADE: https://www.optimade.org/
- NOMAD: https://nomad-lab.eu/
- AiiDA: https://www.aiida.net/
