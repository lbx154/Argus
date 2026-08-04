---
name: "ORCA Quantum Chemistry"
description: "Use an operator-provided authorized ORCA installation for molecular electronic-structure calculations while preserving license boundary, executable/version, input, method, basis, numerical settings, and primary output."
---

## When to use

Use when the project already has authorized access to a specific ORCA version
and ORCA supports the required molecular calculation.

## Do not use when

Do not download, redistribute, or bypass access terms on behalf of the user.
Do not infer that an existing executable has the intended version, optional
components, method defaults, or rights for the requested environment.

## Required inputs

Executable/version and authorization status; source geometry and units;
charge/multiplicity; method, basis/ECP and auxiliary basis; grids, RI/dispersion,
solvation, relativity, SCF/optimization thresholds; resources; and target output.

## Minimum capability probe

Run an authorized small representative input, capture the version banner and
resolved settings, verify charge/spin/geometry, inspect convergence and primary
output, and confirm restart/error behavior.

## Evidence and validation

Retain input, output, executable/version metadata, environment, all method and
numerical options, restart files where needed, warnings, failed states, and
convergence/sensitivity checks. Do not copy restricted software artifacts into
the repository.

## Output contract

Return the computed observable with method, basis, settings, convergence,
uncertainty, primary-output location, authorization caveat, and `computed`
evidence label.

## Stop or replan

Stop when authorization or version cannot be verified, required capability is
absent, the calculation is nonconvergent, or the selected state/model is
scientifically unresolved.

## Official references

- https://www.faccts.de/orca/
- https://www.faccts.de/docs/orca/
