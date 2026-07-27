---
name: Chemical Identity and Representation
description: Normalize and verify molecular, reaction, material, crystal, sample, sequence, and formulation identity before chemistry analysis; use across chemistry domains, not as a substitute for a domain workflow.
category: chemistry-foundation-identity
version: 1
---

## When to use

Use whenever a claim depends on what chemical entity, structure, sample, or
biological construct was actually studied. Apply before database joins, model
inference, calculation setup, route analysis, structure refinement, assay
interpretation, or cross-source comparison.

## Do not use when

Do not treat identifier normalization as proof of purity, structure, reactivity,
phase identity, biological function, or synthetic feasibility. Load the
appropriate domain workflow after identity is made explicit.

## Required identity record

Record only fields relevant to the object:

- Molecule: source representation, stereochemistry, isotopes, formal charge,
  protonation/tautomer assumptions, counterions, solvation, and mixture status.
- Reaction: atom-mapped or otherwise unambiguous reactants, reagents, products,
  stoichiometry, conditions, yield basis, and transformation direction.
- Material or formulation: composition, phase or phase mixture, defect/dopant
  description, processing history, morphology, batch, and sample state.
- Crystal: source CIF or diffraction data, data block, symmetry setting, cell,
  occupancies, disorder model, temperature/pressure, and refinement provenance.
- Biochemical object: sequence, construct boundaries, mutations, tags,
  oligomeric state, cofactors, ligands, post-translational modifications, and
  organism or expression context.

Retain the original input unchanged. Derived canonical forms are additional
fields, never replacements for source data.

## Decision procedure

1. Identify the scientific object and the observable that depends on it.
2. Preserve the original representation and source.
3. Parse with a domain-appropriate deterministic tool when available.
4. Normalize only for the intended operation: deduplication, lookup, modeling,
   calculation, or display may require different forms.
5. Compare independent identifiers or parsers when an identity error could
   change the conclusion.
6. Record every chemically meaningful transformation introduced during
   preparation, including salt stripping, protonation, tautomer selection,
   disorder removal, cell transformation, sequence trimming, or component
   selection.
7. Block rather than guess when two plausible identities produce materially
   different results.

## Validation gates

- Round-trip the normalized representation when the format supports it.
- Check element balance, formal charge, valence, stereochemical completeness,
  and component count for molecular and reaction objects.
- Check composition, occupancy, symmetry, and cell consistency for crystals.
- Check sequence/structure residue mapping, missing residues, alternate
  locations, cofactors, and construct boundaries for biomolecular objects.
- Do not merge records solely because names, formulas, or database search
  results look similar.

## Evidence to retain

Retain source identifiers, source version or retrieval date, original files,
normalization code and options, warnings, derived identifiers, and the mapping
between source and normalized objects.

## Output contract

Report the resolved identity, unresolved ambiguities, transformations applied,
validation results, and which downstream conclusions are sensitive to identity.
Use explicit labels such as `source`, `normalized`, `assumed`, and `unresolved`.

## Stop, block, or replan conditions

Stop when the object cannot be uniquely resolved at the evidence level required
by the task, when normalization drops a meaningful component, or when a
structure fails deterministic parsing without an authorized repair rule.

## Official references

- IUPAC chemical terminology: https://goldbook.iupac.org/
- IUPAC InChI: https://www.inchi-trust.org/
- IUCr CIF specifications: https://www.iucr.org/resources/cif
- wwPDB data standards: https://www.wwpdb.org/documentation
