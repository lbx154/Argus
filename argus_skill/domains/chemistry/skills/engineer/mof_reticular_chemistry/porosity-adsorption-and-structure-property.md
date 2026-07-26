---
name: MOF Porosity Adsorption and Structure Property Workflow
description: Analyze MOF pore geometry, activation, gas or vapor adsorption, transport, and structure-property relationships with raw isotherms, model limits, sample-state linkage, and uncertainty; excludes generic materials property comparison.
category: chemistry-mof-porosity
version: 1
---

## When to use

Use for geometric porosity, surface area, pore-size distribution, adsorption,
separation, diffusion, host-guest, catalysis-related access, or MOF
structure-property analysis.

## Do not use when

Do not infer experimental porosity from an ideal CIF, apply BET or pore models
mechanically, or compare uptake without matching adsorbate, temperature,
pressure/fugacity, basis, and activation state.

## Scientific question

For an identified framework and sample state, what geometric, computed,
simulated, or measured pore/property evidence supports the decision?

## Required inputs

Validated framework structure; guest-removal/activation assumptions; probe or
adsorbate identity; temperature; pressure or fugacity; uptake basis; sample mass
and corrections; raw adsorption/desorption or transport data; equilibration
criteria; model/force-field details; controls and repeats; and the target claim.

## Decision procedure

1. Confirm framework, defects, interpenetration, charge, guests, and activation state.
2. For geometric analysis, define probe radius, atomic radii, periodic cell, and
   accessibility algorithm; distinguish geometric from energetic accessibility.
3. For adsorption, inspect raw isotherms, equilibration, free-space/buoyancy
   corrections, hysteresis, basis, and replicate/sample history.
4. Select BET or pore-size analysis only in a physically justified range/model;
   report the selected region and sensitivity.
5. For simulations, define framework flexibility, charges, force field,
   electrostatics, ensemble, finite-size, initialization, equilibration, and sampling.
6. Compare experiment and simulation only after aligning structure/sample state,
   adsorbate, temperature, pressure/fugacity, basis, and uncertainty.
7. Test alternative explanations including blocked pores, collapse, residual
   solvent, defects, kinetics, impurities, or sample heterogeneity.

## Tool-selection ladder

Use raw instrument data and validated CIFs first; transparent geometric and
isotherm analysis next; established adsorption databases and molecular
simulation tools for documented capabilities; complementary diffraction and
composition evidence to diagnose state changes.

## Minimum capability probe

Reproduce one reported surface-area/uptake value or geometric descriptor from
preserved inputs and units. Verify one pore-access path or isotherm region
manually and inspect model residual/sensitivity.

## Evidence to retain

Keep original/derived structures, activation record, raw isotherms, instrument
metadata, corrections, analysis range/model, simulation inputs/outputs,
versions/seeds, convergence/sampling, repeats, and negative findings.

## Validation gates

- Geometric pore volume and simulated uptake are not measured porosity.
- Surface area is method- and range-dependent, not an intrinsic universal scalar.
- Uptake basis and excess/absolute convention are explicit.
- Structure-property comparisons do not mix activated, hydrated, defective, or
  differently interpenetrated samples.
- Mechanistic selectivity or transport claims require evidence beyond endpoint uptake.

## Uncertainty and applicability domain

Report sample-to-sample variation, model/range sensitivity, detection and
equilibration limits, structural uncertainty, and transfer limits across guests,
conditions, defects, and framework states.

## Output contract

Return framework/sample state, method and conditions, measured versus
computed/simulated values, validation, uncertainty, alternate explanations, and
the narrowest supported structure-property conclusion.

## Stop, block, or replan conditions

Replan when activation or sample state is unknown, raw isotherms are unavailable
for a claim-critical reanalysis, model choice dominates the result, equilibrium
is unsupported, or structure and property evidence refer to different samples.

## Official references

- NIST/ARPA-E Database of Novel and Emerging Adsorbent Materials:
  https://adsorption.nist.gov/
- IUPAC Gold Book: https://goldbook.iupac.org/
- NIST Standard Reference Materials: https://www.nist.gov/srm
