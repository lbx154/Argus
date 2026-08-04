---
name: "Materials Identity Processing and Property Data"
description: "Normalize composition, phase, processing history, sample form, microstructure, and property conditions for metals, ceramics, polymers, composites, and functional materials; excludes crystallographic refinement and MOF topology analysis."
---

## When to use

Use before aggregating literature or database values, training a materials
model, comparing samples, screening candidates, or analyzing
processing-structure-property relationships.

## Do not use when

Do not use as a CIF validation or structure-refinement workflow, and do not
collapse porous coordination frameworks into generic composition records when
linker/node/topology identity matters. Use the crystallography or MOF Skills.

## Scientific question

What material, sample state, processing route, and measurement or calculation
produced the property value, and which records are scientifically comparable?

## Required inputs

- Nominal and measured composition, stoichiometry basis, dopants, impurities,
  phase fractions, defects, and source identity.
- Synthesis and processing history: precursors, atmosphere, temperature-time
  profile, pressure, deformation, annealing, cooling, deposition, curing, aging.
- Sample form and geometry: bulk, powder, film, fiber, porous body, composite,
  interface, or device; thickness, density, orientation, and surface state.
- Microstructure and state: grain/domain size, porosity, texture, morphology,
  crystallinity, molecular weight/crosslinking where relevant.
- Property definition, method, conditions, unit, normalization, uncertainty, and
  whether the value is measured, computed, predicted, or retrieved.

## Identity and normalization

Preserve source formulas, names, files, and sample labels. Create explicit keys
for material system, composition, processing batch, specimen, and measurement.
Do not silently substitute nominal composition for measured composition, a
single phase for a mixture, or an ideal crystal for a processed specimen.

## Decision procedure

1. Define the property and comparison population.
2. Resolve composition and phase/sample identity.
3. Encode processing and specimen history as decision-relevant variables.
4. Normalize units and basis while retaining source values.
5. Detect duplicate upstream measurements across papers and databases.
6. Group splits by material family, prototype, batch, publication, laboratory,
   or time according to the claim.
7. Mark missingness and incompatible protocols instead of filling them with
   unstated defaults.

## Validation gates

- Formula equality alone does not establish the same material state.
- Property values require matching temperature, field, frequency, orientation,
  strain rate, atmosphere, geometry, and normalization where relevant.
- Computed ideal-crystal values are not measured processed-material values.
- A database entry retains its upstream calculation or publication provenance.
- Dataset joins do not leak polymorph, prototype, batch, or duplicate records
  across evaluation groups.

## Evidence to retain

Retain raw source records, extraction location, identity transformations,
composition and process fields, unit conversions, exclusions, conflicts, and
links between specimen and property.

## Output contract

Return a material/sample identity record, process history, property dictionary,
comparability status, provenance, missing fields, and claim-relevant limitations.

## Stop, block, or replan conditions

Block when composition, phase, process state, specimen geometry, or measurement
conditions are too ambiguous for the requested comparison or model claim.

## Official references

- OPTIMADE: https://www.optimade.org/
- NIST Materials Data Repository: https://materialsdata.nist.gov/
- NOMAD: https://nomad-lab.eu/
