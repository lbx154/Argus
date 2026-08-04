---
name: "Chemical Units Conditions and Normalization"
description: "Make units, basis, environmental conditions, sample normalization, and reporting conventions explicit before comparing chemical measurements, calculations, or datasets across sources."
---

## When to use

Use for any quantitative chemistry task involving measurements, computed
properties, formulation, reaction conditions, electrochemistry, spectra,
kinetics, thermodynamics, materials performance, or cross-study comparison.

## Do not use when

Do not use unit conversion to manufacture comparability between different
observables, sample states, protocols, reference electrodes, temperatures,
phases, models, or normalization bases.

## Required inputs

- Original value, unit, significant figures, uncertainty, and source.
- Observable definition and sign convention.
- Normalization basis: mass, area, volume, mole, active material, geometric
  electrode area, BET area, protein concentration, internal standard, or other.
- Conditions that can change the value: temperature, pressure, atmosphere,
  solvent, pH, ionic strength, scan rate, C-rate, frequency, field, wavelength,
  sample geometry, calibration, and elapsed or cycle time as applicable.
- Conversion constants and reference scales.

## Decision procedure

1. Build a data dictionary before aggregation.
2. Preserve source values and units.
3. Convert to one declared analysis unit with a tested library or explicit
   dimensional calculation.
4. Keep the original normalization basis and create a separate converted field.
5. Harmonize reference scales only when the conversion and conditions are known.
6. Compare values only after confirming observable, protocol, sample state, and
   basis are scientifically compatible.
7. Propagate uncertainty and significant-figure limits through conversions.

## Validation gates

- Perform dimensional checks and at least one independently calculated example.
- Reject ambiguous symbols, unitless quantities without definitions, and
  percentages without a denominator or basis.
- For derived values, retain the formula and every source field.
- Flag censored values, detection limits, saturation, extrapolation, and
  instrument-specific arbitrary units.
- Keep missing values distinct from zero, not detected, below quantification,
  not measured, and not applicable.

## Common failure modes

- Comparing gravimetric and areal performance without loading.
- Mixing potentials from different reference electrodes.
- Treating nominal and measured composition as equivalent.
- Converting concentration while ignoring density, hydration, or formulation.
- Pooling spectra or diffraction patterns with incompatible acquisition axes.
- Using room temperature as an unstated universal condition.

## Output contract

Provide a compact table of source value, source unit, normalized value, target
unit, basis, conditions, conversion, uncertainty, and comparability status.
State which comparisons remain invalid after normalization.

## Stop, block, or replan conditions

Block a quantitative comparison when the observable definition, unit, basis, or
materially important condition cannot be recovered. Do not impute a conversion
that determines the conclusion.

## Official references

- IUPAC Green Book: https://iupac.org/what-we-do/books/greenbook/
- BIPM SI Brochure: https://www.bipm.org/en/publications/si-brochure
- NIST SI guidance: https://www.nist.gov/pml/owm/si-units
