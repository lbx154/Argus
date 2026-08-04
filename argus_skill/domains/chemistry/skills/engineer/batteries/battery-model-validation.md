---
name: "Battery Model Boundary and Degradation Validation"
description: "Validate electrochemical models, cycling forecasts, and degradation interpretations against leakage-safe measured cell data; excludes model-free analytical spectroscopy and computational molecular method benchmarking."
---

## When to use
Use after a battery equivalent-circuit, physics-based, statistical, or machine-learning
result is proposed for prediction or interpretation.

## Do not use when
Do not use to call an unvalidated fitted parameter a physical mechanism or to replace
controlled cycling data.

## Scientific question
Does the model add decision-relevant predictive or explanatory value beyond
measurement and a simple baseline under the declared cell/protocol domain?

## Required inputs
Measured normalized data, frozen split/grouping, model inputs/parameters, fit and
prediction outputs, baselines, residuals, and stated claim.

## Identity and normalization
Confirm each feature is available at the prediction time and originates from the same
cell/protocol definition. Mark model output predicted or simulated; reserve measured
for instrument observations.

## Decision procedure
Check parameter identifiability and physical bounds where relevant. Evaluate on
held-out cells, batches, or later time windows with uncertainty and calibration.
Compare to persistence, nominal-capacity, or protocol-aware simple baselines. Test
sensitivity to segmentation, normalization, and missing data. Separate descriptive fit
from prospective prediction and mechanism inference.

## Tool-selection ladder
Use transparent residual/calibration analyses first; then model-specific diagnostics
and independent implementations when claim-critical. Use public archives only with
their provenance and access terms.

## Minimum capability probe
Reproduce a held-out metric and baseline from saved inputs; verify no feature timestamp
postdates its prediction target.

## Evidence to retain
Frozen split, feature availability table, model/version/configuration, parameters,
training logs, predictions, uncertainty, baselines, residuals, sensitivity tests, and
negative results.

## Validation gates
Reject time/group leakage, unreported baseline, unsupported extrapolation, or a model
whose uncertainty covers no useful distinction. Require a mechanism claim to have
orthogonal evidence.

## Common failure modes
Random row splits, using full-life normalization, fitting every cell then calling it
forecasting, nonidentifiable parameters, and presenting simulated voltage as measured.

## Uncertainty and applicability domain
State cell chemistry, protocol, state range, temperature, and horizon limits. Model
success on one cohort does not establish transport or degradation universality.

## Safety and authorization
Do not use an unvalidated model to relax safety limits or operate cells outside
authorized procedures.

## Output contract
State whether the result is a measured association, derived diagnostic, simulated
response, or prediction; provide uncertainty and the narrowest supported use.

## Stop, block, or replan conditions
Replan when timestamps cannot establish leakage safety, protocol shifts confound
performance, or the model fails its claim-critical baseline.

## Official references
- [Battery Data Genome](https://www.energy.gov/eere/vehicles/battery-data-genome)
