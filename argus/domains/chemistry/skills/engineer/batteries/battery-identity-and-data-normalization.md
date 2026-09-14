---
name: "Battery Cell Identity and Electrochemical Data Normalization"
description: "Normalize battery cell, electrode, electrolyte, protocol, and time-series identity before cycling analysis; excludes molecular computation, generic spectroscopy, and biochemical assays."
---

## When to use
Use before comparing electrochemical measurements, public cycling records, or
simulation outputs across cells, batches, protocols, or repositories.

## Do not use when
Do not use to infer molecular mechanisms from quantum calculations or to interpret
instrument modalities outside electrochemical measurements.

## Scientific question
What physical cell and protocol produced each observation, and can values be compared
without changing their denominator, time origin, or operating conditions?

## Required inputs
Preserve raw cycler/potentiostat files and metadata: cell format, electrode chemistry
and loading, area, active-mass definition, electrolyte, separator, formation,
temperature, pressure, voltage window, current/control mode, sampling cadence, and
protocol revision.

## Identity and normalization
Assign immutable cell, electrode/batch, sample, run, and protocol identities. Preserve
raw timestamps and define time zero. Record unit conversions and denominators
(active-material mass, electrode area, cell volume, or energy basis); never silently
interchange them. Mark values measured, retrieved, simulated, predicted, or derived.

## Decision procedure
Make an analysis table with one row per cell/run and explicit grouping keys. Align
cycles from protocol events, not row order. Establish train/validation/test groups by
cell or batch and time before modeling; future cycles, repeats of the same cell, and
postmortem labels must not leak into early-life decisions.

## Tool-selection ladder
Inspect raw exports and metadata first; use project parsers and transparent unit
conversion; then use an electrochemical data capability. PyBaMM, Battery Archive,
BDF, BattINFO, and BEEP are optional capabilities, subject to their documentation and
data terms, not mandatory workflows.

## Minimum capability probe
Parse one complete run and verify voltage, current, capacity, time, cycle boundaries,
and units against the native export and stated protocol.

## Evidence to retain
Raw files, parser/version, normalization code, protocol metadata, excluded records,
unit conversion table, identity map, and negative quality-control findings.

## Validation gates
Block pooled comparison when chemistry, loading, cutoffs, temperature, rate, or
denominator differs without a defensible adjustment. Verify monotonic timestamps and
physically plausible sign/unit conventions.

## Common failure modes
Mixing mAh with mAh g-1, nominal with measured mass, half-cell with full-cell metrics,
calendar with cycle time, and records from a later protocol version.

## Uncertainty and applicability domain
Normalization improves traceability, not comparability across different cell designs or
formation histories. Carry measurement precision and metadata gaps forward.

## Safety and authorization
Analyze authorized data only. Data analysis neither authorizes cell construction nor
overrides laboratory safety controls.

## Output contract
Return a cell/protocol identity record, normalized data reference, conversion choices,
exclusions, leakage-safe split definition, and evidence labels without private
chain-of-thought.

## Stop, block, or replan conditions
Block if raw data or critical protocol metadata are unavailable; replan if a requested
comparison has no common physically meaningful normalization.

## Official references
- [International Electrotechnical Commission](https://www.iec.ch/)
- [U.S. Department of Energy Battery Data](https://www.energy.gov/eere/vehicles)
