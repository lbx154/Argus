---
name: "MOF and Reticular Chemistry Evidence Review"
description: "Independently review MOF framework identity, node/linker connectivity, topology, synthesis and activation, porosity/adsorption, postsynthetic modification, datasets, and generated structures."
---

## When to use

Use for metal-organic frameworks, coordination networks, covalent or reticular
framework studies where periodic connectivity, pore state, and framework
chemistry are central.

## Do not use when

Do not replace crystallographic refinement review, generic materials-property
review, or modality-specific raw-data review. Use those as additional references
when their evidence is claim-critical.

## Review procedure

1. Inspect original/derived CIFs, crystallographic validity, framework/guest atom
   mapping, bonding rules, building-unit reduction, topology, interpenetration,
   disorder, defects, and charge assumptions.
2. Reconstruct precursor, synthesis, washing, exchange, activation,
   modification, storage, and batch/sample linkage from primary evidence.
3. Verify that phase, composition, porosity, adsorption, and property evidence
   refer to the claimed sample state.
4. Inspect raw isotherms or primary property data, units, basis, conditions,
   corrections, model ranges, repeats, and alternatives.
5. For datasets/models/generation, audit provenance, licenses, duplicates,
   topology/building-unit/source leakage, baselines, uncertainty, and staged
   evidence labels.
6. Distinguish reported, inferred, computed, simulated, synthesized, and measured
   claims; do not let one silently replace another.

## Rejection conditions

Return `replan_requested` when framework connectivity or topology is unstable,
sample state is unlinked, coordinated/charge-balancing species were removed
without justification, adsorption analysis lacks raw conditions or valid model
ranges, postsynthetic modification lacks discriminating evidence, dataset
evaluation leaks framework families, or generated/computed structures are
presented as synthesized materials.

## Done standard

Return `done` only at the requested evidence level and identify unresolved
disorder, defects, guest/activation state, topology alternatives, heterogeneity,
and experimental validation gaps. A valuable predicted hypothesis may complete
a screening mission but not a physical discovery objective.
