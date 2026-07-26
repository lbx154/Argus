---
name: Characterization Validation and Multimodal Evidence Integration
description: Validate and integrate diffraction, spectroscopy, MS, NMR, and microscopy evidence without erasing modality limits; excludes battery degradation and computational-method validation.
category: chemistry-characterization
version: 1
---

## When to use
Use after one or more characterization interpretations are proposed for a chemical
identity, phase, purity, structure, or microstructure claim.

## Do not use when
Do not use to average incompatible signals into a stronger claim or to hide
cross-modality disagreement.

## Scientific question
Does the combined measured evidence uniquely support the stated chemical conclusion at
the claimed resolution and sampling scale?

## Required inputs
Original raw data, sample/acquisition metadata, per-modality analysis, calibration,
controls, alternate hypotheses, replicate/field-of-view coverage, and proposed claim.

## Identity and normalization
Verify that all data correspond to the same sample/aliquot and relevant state. Retain
each modality's units, resolution, sampled volume/area, and evidence label; do not
convert inferred assignments into measured facts.

## Decision procedure
Audit each modality independently before integration. Ask whether signals are
orthogonal, merely correlated, or derived from the same assumption/reference. Compare
their sampled regions and times. Use disagreement to identify heterogeneity, sample
change, contamination, or model failure; do not force a consensus.

## Tool-selection ladder
Review native raw data and calibration first, then modality-specific residuals and
replicate statistics, then cross-modal registration or targeted remeasurement if
authorized.

## Minimum capability probe
Trace one claim-critical statement back to its raw observation, calibration, processing
parameters, and sample identity; independently verify units and scale.

## Evidence to retain
Claim-to-evidence table, raw-data links, calibration/controls, per-modality residuals,
sample alignment rationale, discrepant observations, and narrowed/rejected claims.

## Validation gates
Require each modality to pass its own controls before integration. Reject a unique
identity/phase/structure claim when credible alternatives remain unresolved at the
available resolution.

## Common failure modes
Double-counting derived features, combining different aliquots as replicates, treating
spatially local microscopy as bulk composition, and ignoring a blank or reference
failure.

## Uncertainty and applicability domain
Report measurement and assignment uncertainty, heterogeneity, detection limits, and
the scale/time to which conclusions apply.

## Safety and authorization
No retrospective processing can replace a required control or authorized reacquisition.

## Output contract
Provide a supported/narrowed/unsupported conclusion with measured versus inferred
evidence, conflicts, uncertainty, and retained negative results.

## Stop, block, or replan conditions
Replan when sample linkage is uncertain, a claim-critical modality fails calibration,
or disagreement materially changes the conclusion.

## Official references
- [ISO standards catalogue](https://www.iso.org/standards.html)
- [NIST Reference Materials](https://www.nist.gov/srm)
