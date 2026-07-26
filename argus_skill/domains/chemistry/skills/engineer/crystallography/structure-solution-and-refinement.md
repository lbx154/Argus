---
name: Crystal Structure Solution and Refinement Workflow
description: Solve and refine single-crystal or powder structures with data reduction, symmetry tests, chemical constraints, residuals, and alternatives; excludes materials screening and MOF topology.
category: chemistry-crystallography-refinement
version: 1
---

## When to use

Use for solving or refining a periodic crystal structure from diffraction data,
including single-crystal refinement or powder whole-pattern/Rietveld analysis.

## Do not use when

Do not use a visually good fit, database match, or generated CIF as proof of a
correct structure. Do not infer MOF topology or porosity until the crystal model
passes crystallographic and chemical validation.

## Scientific question

What structural model is supported by the observed diffraction data at the
available resolution, and which symmetry, disorder, phase, or composition
alternatives remain?

## Required inputs

Identity-normalized diffraction data and specimen record; data reduction;
candidate cell/symmetry; composition constraints; scattering factors; absorption
model; instrument contribution; phase or starting models; and the intended
claim.

## Decision procedure

1. Inspect data completeness, redundancy, resolution, signal, outliers, and
   reduction diagnostics.
2. Test plausible symmetry and twinning alternatives using observations and
   chemical consistency, not only automated ranking.
3. Solve or select an initial model without importing claim-critical atoms that
   the data cannot support.
4. Refine parameters in a justified order while limiting correlations and
   documenting restraints/constraints.
5. Model disorder, solvent, occupancies, anisotropic displacement, preferred
   orientation, microstructure, phase fractions, and instrument effects only
   when supported.
6. Inspect difference density or pattern residuals and physically structured
   misfit, not only global agreement statistics.
7. Compare plausible alternative models and retain rejected variants.
8. Separate atom/phase identification supported by diffraction from assignments
   requiring complementary composition or spectroscopy.

## Tool-selection ladder

Use raw-data reduction and instrument software appropriate to the experiment;
established crystallographic solution/refinement engines; independent CIF and
geometry validation; and complementary measurements for ambiguous chemistry.
GSAS-II and other packages are capabilities, not evidence by themselves.

## Minimum capability probe

Reproduce the reported cell and one primary residual/statistic from preserved
inputs. Confirm the engine reads the intended wavelength, symmetry, scattering
model, and parameter constraints.

## Evidence to retain

Keep raw/integrated data, reduction and refinement inputs, versions, logs,
reflection or pattern outputs, covariance/correlation information, residual
maps/curves, restraints, alternative models, and final CIF with provenance.

## Validation gates

- Agreement factors are interpreted with data quality and model complexity.
- No occupancy/displacement/disorder model used solely to hide residuals.
- Bonding and geometry checks do not override diffraction evidence but expose
  chemically implausible or over-parameterized models.
- Powder phase quantification requires an adequate instrument/background model,
  phase set, scale/absorption treatment, and uncertainty.
- Hydrogen positions and absolute structure claims match the data's sensitivity.
- Refinement on simulated or predicted data remains simulated evidence.

## Uncertainty and applicability domain

Report parameter uncertainties, correlations, resolution, unmodeled density or
phases, disorder, twinning, preferred orientation, and the structural scale
actually observed.

## Output contract

Return the selected model, data and method, refinement diagnostics, alternate
models, restraints, unresolved features, uncertainty, and claim ceiling. State
which atom, phase, occupancy, or symmetry assignments remain inferred.

## Stop, block, or replan conditions

Replan when competing models are not discriminated, residual structure is
claim-critical, parameter correlation makes the result non-identifiable, or
composition/phase information required by the model is unavailable.

## Official references

- IUCr validation resources: https://www.iucr.org/resources/data/validation
- IUCr CIF: https://www.iucr.org/resources/cif
- GSAS-II: https://subversion.xray.aps.anl.gov/trac/pyGSAS
