---
name: "MOF Datasets Prediction and Structure Generation"
description: "Build and evaluate MOF datasets, property models, screening campaigns, and generated frameworks with topology-aware deduplication, leakage control, synthesizability boundaries, and staged validation."
---

## When to use

Use for MOF database curation, machine-learning property prediction, virtual
screening, linker/node/topology recombination, generative models, or hypothesis
generation.

## Do not use when

Do not call a valid periodic graph a synthesizable MOF, a predicted property a
measurement, or random train/test rows an evaluation of new chemistry. Use
crystallography for CIF validity and synthesis/porosity Skills for physical evidence.

## Scientific question

Define whether the target is data quality, in-domain prediction, family-level
generalization, novel framework generation, computed screening, or experimental
discovery, and set the evidence ceiling accordingly.

## Required inputs

Source structures/data and licenses; original provenance; framework
normalization; node/linker/net labels; guest/solvent/defect policy; property
definitions and conditions; duplicate/grouping keys; baseline; model/generator;
validation oracle; compute/query budget; and novelty reference set.

## Decision procedure

1. Preserve raw structures and source identifiers.
2. Validate crystallography and resolve framework/guest boundaries.
3. Derive topology/building-unit identities under explicit rules.
4. Detect duplicates and near duplicates by provenance, periodic graph,
   building units, topology, composition, and structure as appropriate.
5. Split by source and chemically meaningful families before feature/model selection.
6. Establish simple descriptor and retrieval baselines.
7. Evaluate calibration and out-of-domain behavior, not only average error.
8. For generated structures, test graph validity, periodic geometry, charge and
   coordination plausibility, overlap, density, mechanical/geometric stability,
   and duplication before expensive property calculations.
9. Escalate evidence from generated to computed to synthesized to measured
   without skipping labels.

## Minimum capability probe

Take one known duplicate family and one held-out topology or building-unit
family through normalization, split assignment, model/generator, validation,
and provenance recovery.

## Evidence to retain

Keep source snapshot, licenses, raw/normalized CIFs, transformation rules,
duplicate clusters, split assignments, model/checkpoint/version, seeds,
generated structures, rejection reasons, oracle inputs/outputs, baselines,
uncertainty, and negative results.

## Validation gates

- No random split claim when same framework, source, linker/node family, or
  topology leaks across groups.
- Novelty is checked against a declared dated corpus and does not mean synthesizability.
- Generated geometry is independently validated; model self-scores are not validation.
- Property calculations include convergence and framework-state assumptions.
- Screening methods are compared under equal budgets.
- Experimental discovery requires physical synthesis and characterization evidence.

## Safety and authorization

Generated candidates do not authorize synthesis. Apply precursor, metal, linker,
solvent, activation, and process hazard screening before physical proposals.

## Output contract

Return data lineage and license status, normalization/split logic, baseline and
model results, uncertainty/domain status, generated or ranked structures,
rejections, novelty definition, and the evidence stage reached by each candidate.

## Stop, block, or replan conditions

Block when provenance or licenses are incompatible, topology/identity
normalization is unstable, evaluation leaks related frameworks, or the available
oracle cannot test the requested claim.

## Official references

- Reticular Chemistry Structure Resource: https://rcsr.anu.edu.au/
- OPTIMADE: https://www.optimade.org/
- NIST adsorption database: https://adsorption.nist.gov/
- IUCr CIF: https://www.iucr.org/resources/cif
