---
name: "Biochemistry and Chemical Biology System Identity Normalization"
description: "Normalize sequence, construct, molecular state, ligand, cofactor, mutation, structure, and assay identity; excludes organic synthesis, generic characterization, and atomistic-method selection."
---

## When to use
Use before analyzing binding, kinetics, functional assays, docking, MD, mutagenesis, or
chemical-probe results for a biological target.

## Do not use when
Do not use to treat a protein name as a construct definition, infer activity from a
structure alone, or replace laboratory authorization with planning.

## Scientific question
Which exact biological and chemical system was tested or modeled, in what molecular
state and assay context?

## Required inputs
Retain sequence accession/version and source, construct boundaries/tags, expression and
purification context, mutations, PTMs, oligomeric state, ligand stereochemistry and
state, cofactors/metals, buffer, pH, ionic strength, temperature, structure source,
and assay protocol/readout.

## Identity and normalization
Preserve original sequences/coordinates/data. Map construct sequence to reference
sequence with residue numbering explicitly. Record missing residues, mutations, tags,
cofactor occupancy, protonation assumptions, ligand identity, and experimental versus
modeled structure. Assign immutable sample, construct, batch, plate/run, and protocol
identities. Mark evidence retrieved, measured, computed, simulated, predicted, or
inferred.

## Decision procedure
Determine whether the question concerns direct binding, catalysis, cellular function,
target engagement, or structure. Define relevant biological state, substrate/cofactor,
reaction direction, and controls before selecting an assay or model. Identify
confounders such as aggregation, nonspecific binding, reporter interference, and
construct-dependent behavior.

## Tool-selection ladder
Inspect primary sequence/construct and assay records first; then use validated sequence,
structure, chemistry, or assay analysis capabilities available to the project. Docking
and MD may generate hypotheses, not binding measurements.

## Minimum capability probe
Verify construct-to-reference mapping, ligand/cofactor identity, and one raw assay or
structure record against supplied metadata.

## Evidence to retain
Original inputs, accession/version, construct map, protocol, sample/plate metadata,
calibration/controls, software/version/seeds, raw outputs, and excluded/failed runs.

## Validation gates
Block a mechanistic or affinity claim until construct, molecular state, readout, units,
and controls are known. Confirm assay signal is distinguishable from blank and
interference controls.

## Common failure modes
Residue-numbering drift, unreported tags/mutations, missing cofactors, wrong
protonation, conflating target engagement with function, and reporting a modeled pose
as a bound structure.

## Uncertainty and applicability domain
Identity does not establish physiological relevance or in-cell exposure. Preserve
differences among constructs, species, isoforms, and assay conditions.

## Safety and authorization
Physical work requires approved facilities, biosafety controls, and institutional
authorization. Do not offer procedures to bypass those controls.

## Output contract
Return a system identity record, original/normalized references, assumptions,
controls, and evidence types without private chain-of-thought.

## Stop, block, or replan conditions
Block for unknown construct, cofactor/state, or assay readout; replan when the desired
claim cannot be answered by the available biological system.

## Official references
- [UniProt](https://www.uniprot.org/)
- [Protein Data Bank](https://www.rcsb.org/)
