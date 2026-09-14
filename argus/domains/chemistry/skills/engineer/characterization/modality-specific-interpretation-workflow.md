---
name: "Modality-Specific Chemical Characterization Interpretation Workflow"
description: "Interpret diffraction, spectroscopy, MS, NMR, or microscopy with modality-specific controls and models; excludes one-size-fits-all peak fitting, battery cycling, and molecular simulation."
---

## When to use
Use to answer a bounded composition, structure, phase, bonding, molecular identity, or
microstructure question from one or more characterized samples.

## Do not use when
Do not use a common fitting recipe across diffraction, spectroscopy, MS, NMR, and
microscopy, or claim a measurement that was only predicted/computed.

## Scientific question
Which modality-specific observable, with which controls, supports the requested
chemical interpretation?

## Required inputs
Use normalized raw data and metadata, explicit sample question, calibration/reference,
candidate hypotheses, suitable standards/blanks/replicates, and modality-specific
instrument settings.

## Identity and normalization
Preserve sample and acquisition identity through every processing step. Keep
calibration/reference and axis units attached to each derivative. Label observations
measured, assignments inferred, and library/database matches retrieved.

## Decision procedure
For diffraction, assess instrument geometry, background, peak positions/widths,
preferred orientation, and phase/reference suitability before refinement. For
spectroscopy, inspect baseline, resolution, saturation/absorption, reference and
band-assignment alternatives. For MS, verify mass calibration, polarity, charge/adduct,
isotopic pattern, fragmentation, blanks, and mass error. For NMR, verify reference,
solvent, field, acquisition, phase/baseline, integration, multiplicity, exchange, and
2D correlations. For microscopy, retain original images and assess scale calibration,
contrast mechanism, preparation artifacts, dose/beam damage, fields of view, and
sampling. Use cross-modal consistency as corroboration, not forced agreement.

## Tool-selection ladder
Inspect raw modality-native data first; apply a modality-appropriate processing tool;
compare to certified standards or curated references; use fitting/refinement only when
the model and residuals are inspectable.

## Minimum capability probe
Reproduce a calibrant/reference feature or scale from raw data. For any fit, verify
that a simple visual/raw-data check and residual output are available.

## Evidence to retain
Raw and processed data, metadata, calibrations, standards/blanks, processing/fitting
parameters, residuals, reference/library provenance, alternate assignments, replicate
results, and negative observations.

## Validation gates
Require modality-specific calibration and controls; confirm features exceed relevant
resolution/detection limits; inspect residuals and plausible alternatives. A visually
good fit alone is not a chemical assignment.

## Common failure modes
Overfitting peaks, assigning contaminants to sample, treating library match as proof,
ignoring adducts or NMR exchange, interpreting image contrast as composition without
contrast evidence, and phase identification from one coincident diffraction feature.

## Uncertainty and applicability domain
State the distinction between observed signal and inferred assignment, detection or
quantitation limits, spatial/spectral sampling limits, and untested candidates.

## Safety and authorization
Do not modify raw data to improve agreement. Physical remeasurement requires
authorized instrument access and facility procedures.

## Output contract
Return the question, modality-specific method and controls, measured observations,
inferred assignments, alternative explanations, uncertainty, and links to raw evidence.

## Stop, block, or replan conditions
Stop/replan for missing calibration, unresolved contamination/artifact, nonunique
assignment, fit residuals inconsistent with the model, or inadequate sampling.

## Official references
- [International Union of Crystallography](https://www.iucr.org/)
- [NIST Mass Spectrometry Data Center](https://www.nist.gov/programs-projects/nist-mass-spectrometry-data-center)
- [IUPAC NMR terminology](https://iupac.org/)
