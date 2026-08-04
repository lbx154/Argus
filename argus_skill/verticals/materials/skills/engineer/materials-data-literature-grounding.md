---
name: "Materials Data and Literature Grounding"
description: "Ground material identity, properties, mechanisms, baselines, and novelty in resolvable primary literature and provenance-preserving public databases."
---

# Materials Data and Literature Grounding

## Operating method

1. Normalize the material identity before searching: composition, phase,
   polymorph, structure, defects, microstructure, processing history,
   temperature, pressure, and other state variables that affect the property.
2. Search primary papers and authoritative databases. Use reviews to navigate,
   then verify claim-critical facts in the original source.
3. For database records, retain provider, material or structure identifier,
   query/filter, retrieval date, calculation provenance, units, and any license
   or access condition.
4. Distinguish measured values, first-principles calculations, empirical
   potentials, ML predictions, and values copied from another compilation.
5. Compare only matched conditions. A room-temperature polycrystal measurement
   is not a clean oracle for a 0 K single-crystal calculation without a stated
   model connecting them.
6. Identify the strongest relevant baseline and closest prior method. If novelty
   matters, state the exact known/new boundary; otherwise leave novelty unknown.
7. Preserve missingness and disagreement. Do not average incompatible phases,
   units, processing histories, or measurement methods into a convenient target.

## Useful interfaces

- Materials Project `mp-api` and pymatgen for structures and computed properties;
- OPTIMADE for cross-provider structure queries;
- NOMAD for calculation provenance and archived simulations;
- OpenKIM and ColabFit for interatomic models and reference datasets;
- domain repositories and standards appropriate to the actual material class.

Database values are inputs or comparison evidence, not automatic ground truth.
Check how each value was calculated or measured before using it to calibrate or
validate a model.
