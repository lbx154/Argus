---
name: Computational Chemistry Identity and Input Normalization
description: Normalize molecular or periodic inputs for quantum chemistry, force fields, MD, or free energy; excludes battery analysis, instrument interpretation, and protein assays.
category: chemistry-computational
version: 1
---

## When to use
Use before calculating a molecular or periodic-system property, energy, reaction path,
surface, or trajectory.

## Do not use when
Do not use to interpret raw diffraction, spectroscopy, microscopy, or MS data; to
analyze battery cycling; or to establish biomolecular function from an assay.

## Scientific question
What precisely is the modeled chemical system, and which charge, spin, geometry,
environment, and observable make the calculation scientifically identifiable?

## Required inputs
Retain supplied structures (including source format), composition or cell, isotope and
stereochemical specification, charge, multiplicity or magnetic order, intended state,
environment, target observable, and known experimental conditions. Record whether
each item is retrieved, assumed, predicted, or measured.

## Identity and normalization
Preserve the original file unchanged. Create a normalized, labeled input only after
checking atom count/order, bond topology, stereochemistry, protonation/tautomer,
counterions, fragments, periodic boundary conditions, cell vectors, defects, and
occupancies. State the conformer source and coordinate units. For open-shell,
transition-metal, radical, or periodic magnetic systems, enumerate credible
spin/oxidation or magnetic states rather than silently choosing one.

## Decision procedure
Map the requested observable to the physical state and ensemble. Separate a gas-phase
model from solvated, condensed-phase, surface, or finite-temperature claims. List
plausible alternative identities that could change the conclusion and select or defer
them with a reason.

## Tool-selection ladder
First inspect native structures and metadata; then use a structure validator or
visualizer; then use a chemistry toolkit for format/topology checks; use an
electronic-structure or simulation engine only after identity is fixed. A tool is a
capability, not evidence by itself.

## Minimum capability probe
Parse the original and normalized inputs, compare composition/charge/cell, and render
or inspect coordinates. For periodic inputs, verify lattice and fractional/cartesian
conventions; for molecular inputs, verify multiplicity is compatible with electron
count.

## Evidence to retain
Original and normalized inputs, conversion logs, identity decisions and alternatives,
software/version, units, hashes where useful, and failed parses or rejected states.

## Validation gates
Do not begin production calculations until the requested observable, model boundary,
identity, units, and state assumptions are explicit and internally consistent.

## Common failure modes
Neutralizing an ionic system unintentionally; default singlets for radicals; duplicate
fragments; an implicit-solvent claim on gas-phase coordinates; and treating a crystal
structure as the unique finite-temperature conformation.

## Uncertainty and applicability domain
Normalization cannot establish that a chosen protonation, defect, conformation, or
spin state is populated. Carry those alternatives into calculation or limit the claim.

## Safety and authorization
Use only authorized compute and licensed inputs. Do not claim physical synthesis,
measurement, or safety clearance from a model input.

## Output contract
Deliver a compact identity record, normalized inputs, explicit alternatives, and a
statement of what is assumed versus retrieved or measured. Do not retain private
chain-of-thought.

## Stop, block, or replan conditions
Block if the molecular/periodic identity or target state is indeterminate. Replan if
the requested claim requires an environment or timescale not represented by the model.

## Official references
- [IUPAC Gold Book](https://goldbook.iupac.org/)
- [CODATA recommended values](https://physics.nist.gov/cuu/Constants/)
