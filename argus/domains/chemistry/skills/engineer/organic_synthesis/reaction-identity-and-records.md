---
name: "Organic Reaction Identity and Records"
description: "Normalize organic reaction components, mapping, stoichiometry, stereochemistry, conditions, workup, and yields for route or dataset work; excludes biochemical assays and materials processing."
---

## When to use

Use for literature or patent reaction extraction, reaction database curation,
route comparison, forward prediction, retrosynthesis inputs, condition analysis,
or reproduction of a reported organic transformation.

## Do not use when

Do not use for enzyme kinetics, metabolic pathways, battery cycling, bulk
materials processing, or crystal-structure refinement. Do not infer a complete
procedure from a reaction scheme alone.

## Scientific question

Which exact transformation, components, conditions, outcome, and source evidence
does this reaction record represent, and is it complete enough for the intended
retrieval, dataset, route, or reproducibility decision?

## Required inputs

- Target and precursor structures with stereochemistry, isotope labels, charge,
  salt/solvate state, and source identifiers.
- Component roles as reported: reactant, reagent, catalyst, ligand, solvent,
  additive, quench, workup, purification, or product.
- Amounts, concentration, order/rate of addition, atmosphere, temperature-time
  profile, pressure, vessel or irradiation/electrochemical conditions.
- Isolation and analytical evidence, yield type and basis, selectivity, scale,
  and source location.

## Identity and normalization

Preserve the source reaction unchanged. Create a normalized reaction as a
separate object. Check atom and element balance where the record is intended to
be balanced, but do not force catalysts, salts, or unreported byproducts into a
fabricated equation. Record unmapped atoms and ambiguous component roles.
Preserve protecting groups, regiochemistry, stereochemical outcomes, and
mixtures.

## Decision procedure

1. Resolve structures and components from primary experimental text and files.
2. Separate reaction, workup, and purification operations.
3. Normalize quantities and conditions while retaining source values.
4. Validate molecular parsing and, when available, atom mapping with an
   independent chemical sanity check.
5. Mark missing, inferred, and conflicting fields explicitly.
6. Deduplicate by normalized transformation plus source, substrate context, and
   conditions rather than by product name alone.

## Validation gates

- No yield without type: isolated, assay, conversion-derived, crude, or unknown.
- No reagent/catalyst role assignment based only on database column position.
- No stereochemical claim when the source structure or analytical evidence is
  unspecified.
- No procedure-level recommendation from a source that omits scale, workup, or
  safety-critical conditions.
- Conflicting source text, scheme, and supplementary data remain visible.

## Evidence to retain

Retain DOI/patent/database identifiers, exact source location, source structures,
normalized structures, extraction method, transformations, warnings, and
confidence per field.

## Output contract

Return a structured reaction record plus a missingness/conflict report. State
whether it supports transformation identity, condition reuse, yield comparison,
route feasibility, or only retrieval.

## Stop, block, or replan conditions

Block when target or product identity is ambiguous, when the record lacks the
conditions required by the downstream decision, or when normalization changes
the reported stereochemical or component meaning.

## Official references

- Open Reaction Database: https://open-reaction-database.org/
- ORD schema documentation: https://docs.open-reaction-database.org/
- IUPAC stereochemical terminology: https://goldbook.iupac.org/
