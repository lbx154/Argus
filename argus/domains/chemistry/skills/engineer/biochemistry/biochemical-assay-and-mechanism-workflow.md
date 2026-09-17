---
name: "Biochemical Binding, Kinetics, and Functional Assay Workflow"
description: "Analyze biochemical affinity, kinetics, inhibition, target engagement, or function with construct, cofactor, state, and assay controls; excludes docking-only claims and generic characterization."
---

## When to use
Use for a bounded analysis of existing authorized biochemical, biophysical, or
chemical-biology assay data, or for planning its evidence logic.

## Do not use when
Do not use a docking score, MD trajectory, or one endpoint screen as proof of affinity,
mechanism, selectivity, or functional causality.

## Scientific question
Under defined construct, ligand, cofactor, and assay conditions, what measured binding,
kinetic, engagement, or functional effect is supported?

## Required inputs
Use normalized system identity, raw assay data, protocol/readout, concentration series,
replicates, blanks, positive/negative controls, calibrations, timing, and predefined
analysis model. Retain authorized experimental context only.

## Identity and normalization
Carry construct, lot/batch, ligand state, cofactors, buffer/pH, temperature, and plate
or instrument run into analysis. Preserve raw readings; distinguish measured signal,
derived concentration/rate/affinity, and model-estimated parameters.

## Decision procedure
Choose an assay/readout that addresses the question and define control logic before
looking at outcomes. For binding, test signal stability, concentration range,
stoichiometry/aggregation/interference controls, and orthogonal confirmation where
claim-critical. For kinetics, establish initial-rate regime, substrate/cofactor ranges,
enzyme concentration, time window, product/readout linearity, and competing models.
For functional claims, separate target engagement from downstream phenotype and use
appropriate genetic/chemical/vehicle controls.

## Tool-selection ladder
Inspect raw instrument/plate output and controls first; use transparent fitting and
residual diagnostics next; use orthogonal assay analysis or structural/computational
hypotheses only to clarify discordance.

## Minimum capability probe
Reproduce a blank-corrected control and a representative replicate from raw readings.
For a fit, verify units, residuals, confidence intervals, and a plausible null model.

## Evidence to retain
Raw files, protocol/version, plate/run maps, reagent/construct identity, controls,
replicate-level data, calibration, fitting code/version, residuals, model comparison,
failed runs, and deviations.

## Validation gates
Require valid controls, replicate-aware uncertainty, a signal in range, and model
diagnostics. Verify that the claimed parameter is identifiable under the sampled
concentrations and that inhibition/activation is not reporter interference or
aggregation.

## Common failure modes
Using endpoint data as initial rates, overfitting Hill slopes, ignoring depletion,
mixing technical and biological replicates, treating IC50 as Ki without conditions,
and confusing cellular phenotype with direct binding.

## Uncertainty and applicability domain
Report measured readout separately from derived affinity/kinetic parameters; state
construct, buffer, pH, cofactors, temperature, concentration range, and model limits.

## Safety and authorization
Do not execute wet-lab procedures outside authorized facilities or controls. Analysis
does not grant authorization for biological materials or experiments.

## Output contract
Return question, system/conditions, measured observations, fitted/derived parameters,
controls, uncertainty, negative results, and only the supported mechanistic boundary.

## Stop, block, or replan conditions
Stop/replan for failed controls, missing raw data, nonidentifiable fit, saturation or
interference artifacts, or a claim requiring an orthogonal assay not available.

## Official references
- [IUPAC biochemical nomenclature](https://iupac.org/what-we-do/nomenclature/)
- [NIH rigor and reproducibility](https://grants.nih.gov/policy-and-compliance/policy-topics/reproducibility)
