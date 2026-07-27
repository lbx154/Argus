---
name: CIF and Crystal Structure Validation
description: Validate CIF syntax, symmetry, composition, geometry, occupancies, displacement parameters, refinement evidence, and provenance; excludes de novo refinement and MOF topology or activation.
category: chemistry-crystallography-validation
version: 1
---

## When to use

Use for deposited, published, database, generated, converted, or refined CIFs
before simulation, comparison, publication, model training, or domain analysis.

## Do not use when

Do not declare a structure experimentally validated merely because it parses,
passes geometry checks, or resembles a database entry. Generated structures and
database records retain their original evidence class.

## Scientific question

Is this crystal structure internally consistent, source-traceable, and
scientifically fit for the intended comparison, calculation, publication, or
domain analysis?

## Required inputs

Original CIF and data block; source/deposition/publication; structure-factor or
powder data if available; validation report; refinement metadata; composition
and sample context; and any transformations or repairs.

## Decision procedure

1. Parse the CIF with a standards-aware reader and preserve warnings.
2. Resolve data block, dictionary version, symmetry operations, setting, cell,
   coordinate convention, and atom labels.
3. Recalculate composition and cell contents from sites, multiplicities, and
   occupancies.
4. Check duplicate sites, minimum distances, bonding plausibility, coordination,
   displacement parameters, disorder, solvent, and charge balance where meaningful.
5. Compare reported and recalculated geometry/statistics.
6. Inspect source refinement evidence and validation alerts.
7. Distinguish safe syntactic normalization from scientific repair; every repair
   gets a separate derived file and rationale.
8. For generated or relaxed structures, test geometry and model consistency but
   retain `predicted` or `computed` status.

## Minimum capability probe

Round-trip one CIF and verify cell, symmetry operations, formula, site count,
occupancies, and fractional coordinates. Confirm no silent symmetry expansion,
site loss, or coordinate wrapping changes identity.

## Validation gates

- No hidden deletion of disordered solvent, partial occupancy, hydrogen atoms,
  counterions, or alternate models.
- CheckCIF-style alerts are interpreted, not counted as pass/fail tokens.
- Chemical plausibility cannot erase unresolved diffraction residuals.
- A relaxed structure is not the deposited experimental model.
- Structure-factor absence limits independent refinement validation.

## Evidence to retain

Retain original and derived CIFs, parser/version, validation reports, source
identifiers, transformation logs, recalculated fields, alerts, and unresolved
issues.

## Output contract

Return provenance and evidence class, syntax/semantic status, composition and
symmetry checks, geometry/occupancy/displacement alerts, refinement evidence
availability, transformations, and fitness for the intended downstream use.

## Stop, block, or replan conditions

Block downstream claim-critical use when the CIF cannot be parsed reproducibly,
composition or symmetry is inconsistent, severe contacts/occupancies are
unresolved, or source provenance and evidence class are unknown.

## Official references

- IUCr CIF: https://www.iucr.org/resources/cif
- IUCr checkCIF: https://checkcif.iucr.org/
- IUCr data validation: https://www.iucr.org/resources/data/validation
