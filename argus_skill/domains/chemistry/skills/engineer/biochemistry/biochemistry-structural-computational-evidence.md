---
name: "Biochemistry Structural, Docking, and MD Evidence Boundary Workflow"
description: "Evaluate structures, docking, MD, and mutations as biochemical hypotheses with construct, protonation, cofactor, and assay boundaries; excludes affinity proof and generic method benchmarking."
---

## When to use
Use when structural models, docking, MD, or mutations are proposed to explain or
prioritize a biochemical binding or functional hypothesis.

## Do not use when
Do not use computational pose scores or stable trajectories as measured affinity,
selectivity, mechanism, or in-cell activity.

## Scientific question
Given a defined construct and molecular state, which structural hypotheses are
consistent with, but not proven by, available biochemical evidence?

## Required inputs
Use sequence/construct map, structure source/resolution or model provenance, ligand
identity/stereochemistry, protonation/cofactor/metal states, mutations, docking/MD
inputs and outputs, and relevant assay evidence.

## Identity and normalization
Map residues and ligands across sequence, construct, and coordinates. Preserve original
structures and trajectories; record missing regions, alternate conformers, waters,
cofactors, protonation assumptions, force field/model, box/ions, seeds, and units.
Label structures measured or modeled; poses predicted and trajectories simulated.

## Decision procedure
First assess whether a structure represents the relevant construct/state. Enumerate
plausible ligand protonation, tautomer, cofactor, metal, and binding-site states.
Treat docking as pose generation/ranking and MD as sampling conditional on its model.
Test whether proposed contacts predict discriminating mutations or assay changes, then
compare with independently measured controls; account for mutations that affect
folding/expression rather than binding.

## Tool-selection ladder
Inspect deposited/primary structural evidence and raw assay results first; use docking
or MD for hypothesis generation; use independent structural/biophysical evidence when
a conclusion depends on pose or affinity.

## Minimum capability probe
Verify residue numbering, ligand/cofactor state, and one reported contact against the
source structure; confirm trajectory topology matches its coordinates and units.

## Evidence to retain
Source structures and metadata, normalized inputs, parameter files/versions/seeds,
primary docking/trajectory outputs, pose/trajectory diagnostics, mutation rationale,
assay raw data/controls, and failed alternative states.

## Validation gates
Require chemical plausibility and reproducible setup; test sensitivity to
claim-critical states. A pose/trajectory must not be elevated above hypothesis without
orthogonal measured evidence. Check mutations for protein integrity controls.

## Common failure modes
Wrong protonation/metal state, numbering mismatch, ignoring missing loops, one docking
pose selected after the fact, insufficient MD sampling, and interpreting loss-of-
function mutation as contact proof.

## Uncertainty and applicability domain
State model, sampling, force-field, and structural-resolution limitations. Restrict
conclusions to generated structural hypotheses unless measurements discriminate them.

## Safety and authorization
Respect structural-data terms and authorized compute. Structural hypotheses do not
authorize biological intervention.

## Output contract
Return measured/retrieved versus predicted/simulated evidence separately, alternatives,
testable hypotheses, and an explicit ceiling on affinity/mechanism claims.

## Stop, block, or replan conditions
Replan for ambiguous construct/state, incompatible assay and model evidence, inadequate
sampling, or no discriminating experimental control.

## Official references
- [wwPDB](https://www.wwpdb.org/)
- [Protein Data Bank](https://www.rcsb.org/)
