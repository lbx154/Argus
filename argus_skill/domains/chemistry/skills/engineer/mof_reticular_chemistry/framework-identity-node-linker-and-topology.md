---
name: "MOF Framework Identity Node Linker and Topology"
description: "Resolve metal-organic framework identity from source structures into building units, connectivity, interpenetration, defects, guests, and net topology; excludes generic CIF validation and non-framework materials screening."
---

## When to use

Use for MOF or coordination-network literature review, structure comparison,
node/linker decomposition, topology assignment, coordination-environment
analysis, duplicate detection, or dataset construction.

## Do not use when

Do not use for generic molecular crystals, dense inorganic materials, or CIF
syntax/refinement certification. Apply crystallography Skills first when the
underlying structure is not trustworthy.

## Scientific question

What periodic framework is actually represented, how are its inorganic and
organic building units connected, and which topology, interpenetration, defect,
or guest assumptions are supported?

## Required inputs

Original CIF and source/deposition; crystallographic validation status; reported
name/formula; synthesis and activation state; atom/site labels; bond/connectivity
information if present; disorder/occupancies; counterions and guests; and any
reported topology.

## Identity and normalization

Preserve the original experimental or generated structure. Create separate
derived representations for disorder resolution, solvent removal, bond
perception, primitive-cell conversion, and topology analysis. Record oxidation
and protonation assumptions, coordination bonds added/removed, periodic images,
missing atoms, catenation/interpenetration, and charge-balancing species.

## Decision procedure

1. Confirm the source CIF's crystallographic and chemical fitness.
2. Distinguish framework atoms from pore guests, coordinated solvent,
   counterions, modulators, defects, and unresolved density.
3. Infer connectivity using chemically justified distance/coordination rules and
   test sensitivity to plausible alternatives.
4. Identify metal ions/clusters or secondary building units and organic linkers;
   preserve cases where decomposition is non-unique.
5. Reduce the periodic graph with a stated convention and assign a net only when
   graph equivalence supports it.
6. Record dimensionality, interpenetration, multinet structure, edge
   multiplicity, and defects.
7. Compare frameworks by graph and building-unit identity, not only name,
   formula, cell, or pore size.

## Tool-selection ladder

Use standards-aware crystallographic parsers and visualization first;
deterministic periodic graph analysis next; then topology databases or
net-identification tools. A reported or software-assigned net is retrieved or
computed evidence, not self-validating truth.

## Minimum capability probe

Round-trip the CIF, reproduce composition and periodic bonding, and manually
verify one metal coordination environment and one linker connection across a
cell boundary. Confirm topology assignment is stable to the documented
connectivity tolerance.

## Evidence to retain

Keep original/derived CIFs, source, transformations, framework/guest atom maps,
bonding rules, building-unit decomposition, periodic graph, topology candidates,
interpenetration, warnings, and rejected alternatives.

## Validation gates

- No automatic solvent removal of coordinated or charge-balancing species.
- No topology assignment from visual resemblance or framework name.
- No unique node/linker decomposition when multiple chemically plausible
  reductions exist.
- Missing atoms, disorder, partial occupancy, and charge imbalance propagate to
  downstream uncertainty.
- Generated frameworks remain predicted until experimental evidence exists.

## Output contract

Return framework identity, evidence class, building units, coordination
environments, graph construction rules, topology and alternatives,
interpenetration/defects/guests, transformations, and fitness for downstream use.

## Stop, block, or replan conditions

Block topology or descriptor claims when the source structure is invalid,
connectivity is unstable to plausible choices, disorder/missing atoms alter the
net, or the framework/guest boundary is unresolved.

## Official references

- Reticular Chemistry Structure Resource: https://rcsr.anu.edu.au/
- IUCr CIF: https://www.iucr.org/resources/cif
- Crystallography Open Database: https://www.crystallography.net/cod/
