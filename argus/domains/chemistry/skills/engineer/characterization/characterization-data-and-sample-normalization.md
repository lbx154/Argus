---
name: "Characterization Sample, Instrument Data, and Metadata Normalization"
description: "Preserve and normalize raw diffraction, spectroscopy, MS, NMR, or microscopy data and modality metadata; excludes generic peak fitting, battery cycling, and calculation input preparation."
---

## When to use
Use before interpreting chemical characterization data from an instrument or comparing
measurements across samples and sessions.

## Do not use when
Do not use to overwrite native files, collapse modalities into a generic peak table, or
infer cell cycling performance or biological function.

## Scientific question
What sample, preparation, instrument state, acquisition method, and calibration
produced each raw observation?

## Required inputs
Retain native raw files, acquisition software/version, sample identifier and history,
preparation, instrument/method settings, calibration/reference, environment, units,
operator annotations, and any processing already applied.

## Identity and normalization
Assign immutable sample, aliquot, preparation, acquisition, and processing identities.
Copy raw data read-only; make derived open/normalized representations separately.
Record modality-specific axes and units: e.g., 2theta/Q/d-spacing, wavelength,
chemical shift/reference, m/z/charge/polarity, pixel scale/dose, or spectral
wavenumber. Label data measured, retrieved, or simulated; label peak assignments
inferred, never measured.

## Decision procedure
Assess raw-data integrity and calibration before baseline correction, normalization,
binning, image processing, or peak picking. Keep every transformation parameter and
avoid applying a processing method across modalities without a physical rationale.
Define blanks, standards, replicates, and controls from the experimental design.

## Tool-selection ladder
Use vendor-native viewers/exporters and raw metadata first; then an appropriate
modality-specific parser/analysis capability. Use open formats only when conversion
preserves original data and metadata.

## Minimum capability probe
Open a representative raw file, extract acquisition metadata, and compare one axis,
calibrant/reference, and intensity/count field with the native display.

## Evidence to retain
Read-only raw files, metadata exports, calibration/standard records, conversion and
processing scripts/versions, derived files, failed imports, and sample provenance.

## Validation gates
Do not interpret data without sample identity, modality, axis units, and acquisition
conditions. Confirm that derived data can be traced to unmodified raw data.

## Common failure modes
Replacing raw data with screenshots, losing polarity or reference information,
confusing counts with normalized intensity, undocumented background subtraction, and
sharing one calibration across incompatible sessions.

## Uncertainty and applicability domain
Normalization cannot correct an unsuitable acquisition, degraded sample, or missing
control. Record resolution, detection limits, and metadata gaps.

## Safety and authorization
Use authorized instrument data and follow facility retention rules. Analysis does not
authorize instrument operation or sample handling.

## Output contract
Return a sample/acquisition identity map, raw-data locations, normalized derivatives,
processing lineage, calibration status, and unresolved metadata without private
chain-of-thought.

## Stop, block, or replan conditions
Block when raw data or critical calibration/acquisition metadata are unavailable;
reacquire or narrow claims when resolution or controls cannot answer the question.

## Official references
- [NIST data and measurement resources](https://www.nist.gov/data)
- [IUPAC Gold Book](https://goldbook.iupac.org/)
