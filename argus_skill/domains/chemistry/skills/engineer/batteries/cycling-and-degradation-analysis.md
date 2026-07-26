---
name: Battery Cycling and Degradation Analysis Workflow
description: Analyze cycling, rate, impedance, and degradation trajectories with cell/protocol controls and leakage-safe evaluation; excludes materials discovery, quantum chemistry, and generic peak fitting.
category: chemistry-batteries
version: 1
---

## When to use
Use to quantify capacity retention, coulombic/energy efficiency, resistance evolution,
rate behavior, or early-life prediction from identified cell data.

## Do not use when
Do not use to claim a degradation mechanism without complementary evidence or to
compare cells with incompatible protocols as though they were replicates.

## Scientific question
Under defined cycling conditions, what measured electrochemical trajectory and
uncertainty support a performance or degradation conclusion?

## Required inputs
Use normalized cell/run data, protocol metadata, quality criteria fixed before
inspection, controls/replicates, target metric, and any model specification.

## Identity and normalization
Carry cell, batch, and protocol identities into every aggregate. Preserve raw time
series; label derived capacity, resistance, SOH, and model estimates distinctly from
measured channels.

## Decision procedure
First plot raw voltage/current/capacity against time and cycle. Segment formation,
rest, cycling, pulse, and diagnostic blocks from protocol metadata. Compute metrics
with explicit formulae and denominators; summarize by independent cell, then group.
For degradation, separate loss of lithium inventory, active material, resistance, and
protocol artifacts unless evidence resolves them. For forecasting, split by time and
cell/batch before feature selection; compare against simple protocol-aware baselines.

## Tool-selection ladder
Use native files and transparent analysis first; use battery-data tooling for parsing
or diagnostics; use PyBaMM only when a declared physical model addresses the question.
Do not convert a model fit into a measurement.

## Minimum capability probe
Recompute one cycle's charge/discharge capacity, efficiency, and voltage limits from
raw samples. Verify a diagnostic feature is stable under reasonable segmentation
choices.

## Evidence to retain
Raw and cleaned data references, formulas/code/version, protocol, cell-level plots,
replicate distribution, exclusions, fitted parameters, baselines, split membership,
residuals, and failed models.

## Validation gates
Require protocol comparability, independent replicate reporting, uncertainty intervals,
and leakage-free evaluation. A prediction must beat an appropriate baseline on held-out
cells or time; a fit must show residual and identifiability checks.

## Common failure modes
Reporting group averages without cells, using post-failure data to predict failure,
cycle-number alignment across changed cutoffs, confusing capacity fade with energy
fade, and attributing a mechanism from one diagnostic.

## Uncertainty and applicability domain
Report measured versus model-derived quantities, between-cell variation, and data
coverage. Do not generalize across chemistry, geometry, temperatures, or protocols
outside the observed domain.

## Safety and authorization
Do not use analysis to direct unsafe charging, discharging, or teardown. Physical
actions require authorized equipment, procedures, and interlocks.

## Output contract
Return cell-level and aggregate results, conditions, formulas, uncertainty, baseline
comparison, exclusions, and a bounded measured/model-derived interpretation.

## Stop, block, or replan conditions
Stop for missing protocol metadata, corrupted raw channels, non-independent
replicates, or apparent performance driven by leakage or changed operating conditions.

## Official references
- [IEC Technical Committee 21](https://www.iec.ch/dyn/www/f?p=103:7:0::::FSP_ORG_ID:1271)
- [PyBaMM documentation](https://docs.pybamm.org/)
