---
name: Crystallography Evidence Review
description: Independently review diffraction data, symmetry, single-crystal or powder refinement, CIF validity, phase claims, and structural uncertainty; excludes generic characterization review, materials discovery, and MOF topology assessment.
category: chemistry-review-crystallography
version: 1
---

## When to use

Use for crystal-structure solution/refinement, phase identification or
quantification, CIF use, and diffraction-supported structural claims.

## Do not use when

Do not replace modality-wide characterization review or MOF framework/topology
review. This rubric addresses crystallographic evidence.

## Review procedure

1. Inspect original raw/integrated diffraction data, reduction, wavelength,
   geometry, specimen identity, and source CIFs where available.
2. Reconstruct cell, symmetry setting, transformations, phase model, twinning,
   disorder, occupancies, restraints, and refinement path.
3. Check completeness/resolution, residual maps or pattern residuals, agreement
   statistics, parameter correlations, and alternative symmetry/phase models.
4. Recalculate composition, site multiplicities, occupancies, and key geometry.
5. Interpret validation alerts in context and inspect claim-critical raw evidence.
6. Distinguish diffraction-supported assignments from chemical inference and
   generated/computed structures from measured structures.

## Rejection conditions

Return `replan_requested` when source data are unavailable for a claim requiring
independent validation, competing models remain unresolved, refinement hides
misfit with unjustified parameters, composition/symmetry is inconsistent,
phase quantification lacks an adequate model, or a parseable/generated CIF is
presented as experimental proof.

## Done standard

Return `done` only for the structural resolution and evidence actually
supported. State unresolved disorder, light atoms, absolute structure, phase
fraction, local versus average structure, and data-availability limitations.
