---
name: Materials Science Evidence Review
description: Independently review non-MOF materials identity, processing, property data, discovery, and processing-structure-property claims; excludes crystal-refinement certification, MOF topology, battery cycling, and generic spectroscopy review.
category: chemistry-review-materials
version: 1
---

## When to use

Use for metals, ceramics, polymers, composites, thin films, catalysts, and other
functional-material studies where composition, processing, structure, and
property are linked.

## Do not use when

Route crystallographic validity, MOF framework-specific claims, battery cell
performance, and modality-specific instrument assignments to their dedicated
review Skills.

## Review procedure

1. Reconstruct material, phase, batch, specimen, processing, geometry, and
   measurement conditions from original evidence.
2. Check unit, normalization, property definition, and comparability.
3. Inspect raw data, calibrations, controls, independent specimens/batches, and
   exclusions.
4. Audit dataset provenance, grouping, duplicates, leakage, baselines,
   uncertainty, and applicability for prediction/discovery claims.
5. Distinguish retrieved, predicted, computed, synthesized, characterized, and
   measured evidence.
6. Test confounders including phase fraction, density, texture, porosity,
   thickness, contamination, aging, and protocol differences.
7. Require discriminating evidence before accepting a mechanism.

## Rejection conditions

Return `replan_requested` when identity or process history is ambiguous,
comparisons mix incompatible specimens or methods, evaluation leaks related
materials, rankings lack a meaningful baseline, effects are within uncertainty,
or computed/predicted values are presented as physical validation.

## Done standard

Return `done` only for the stated material family, process window, specimen form,
conditions, and evidence level. A candidate list can complete a screening task;
it cannot complete a synthesis or measured-property objective.
