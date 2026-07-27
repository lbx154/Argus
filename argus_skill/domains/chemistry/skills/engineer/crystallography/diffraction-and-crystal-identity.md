---
name: Diffraction and Crystal Structure Identity
description: Normalize single-crystal or powder diffraction identity, wavelength, geometry, symmetry, cell, phases, and CIF provenance; excludes generic spectral interpretation and MOF topology.
category: chemistry-crystallography-data
version: 1
---

## When to use

Use before single-crystal structure solution, powder indexing or Rietveld
refinement, CIF comparison, phase analysis, or crystallographic database joins.

## Do not use when

Do not use as a generic peak-fitting workflow for spectroscopy, as proof of
chemical purity, or as MOF net/linker analysis. Diffraction evidence may require
complementary chemical characterization.

## Scientific question

Which specimen, radiation, geometry, reciprocal-space or powder dataset,
symmetry convention, phase model, and environmental conditions define the
crystallographic problem?

## Required inputs

- Unmodified detector frames or integrated reflection/pattern data when
  available, with instrument and processing metadata.
- Sample/batch identity, composition evidence, crystal selection or powder
  preparation, temperature, pressure, atmosphere, and history.
- Radiation source, wavelength, geometry, detector calibration, scan strategy,
  resolution, absorption and integration settings.
- Proposed cell, symmetry/setting, phase list, twinning/disorder information,
  and source CIF or model.

## Identity and normalization

Preserve raw data and source CIFs. Record transformations between primitive,
conventional, reduced, and alternate settings; maintain atom/site mappings.
Keep fractional and Cartesian coordinates explicit. Preserve occupancies,
anisotropic displacement parameters, disorder parts, restraints, and symmetry
operators. Do not identify structures only by reduced formula.

## Decision procedure

1. Verify sample, data, wavelength, geometry, and calibration identity.
2. Check cell and reciprocal/powder indexing consistency.
3. Establish candidate symmetry from systematic absences, intensity statistics,
   metric symmetry, and chemistry without treating software suggestions as proof.
4. Identify phase mixtures, twins, modulation, diffuse scattering, preferred
   orientation, or texture that may invalidate a simple model.
5. Normalize settings only with an explicit reversible transformation.
6. Carry all ambiguities into solution/refinement rather than silently choosing
   the most convenient model.

## Minimum capability probe

Open one raw or integrated dataset and one source CIF; verify wavelength, cell,
reflection/pattern range, symmetry setting, composition, coordinate convention,
and a round-trip or independent parse.

## Validation gates

- CIF syntax validity is not structural validity.
- Formula, site multiplicities, occupancies, charge/chemistry, and cell contents
  must be mutually plausible.
- A database cell match alone does not establish phase identity.
- A transformed structure retains a documented atom/site correspondence.
- Missing raw data, unmerged reflections, or processing logs lower the reviewable
  evidence level.

## Evidence to retain

Retain raw and integrated data, calibration, processing logs, source/normalized
CIFs, setting transformations, candidate symmetries/phases, rejected models,
and warnings.

## Output contract

Return data and specimen identity, crystallographic setting, candidate
symmetry/phases, transformations, unresolved ambiguities, and whether the inputs
are fit for solution, refinement, comparison, or only preliminary screening.

## Stop, block, or replan conditions

Block when wavelength, cell, axis convention, sample identity, or source data
cannot be established, or when a simple periodic phase model cannot represent
claim-critical disorder, twinning, or multiphase evidence.

## Official references

- IUCr CIF: https://www.iucr.org/resources/cif
- IUCr crystallographic resources: https://www.iucr.org/resources
- Crystallography Open Database: https://www.crystallography.net/cod/
