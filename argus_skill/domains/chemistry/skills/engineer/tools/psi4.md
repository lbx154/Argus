---
name: Psi4 Quantum Chemistry
description: Use Psi4 for molecular electronic-structure calculations with explicit molecular state, method, basis, options, convergence, units, and primary-output provenance.
category: chemistry-tool-computational
version: 2
---

## When to use

Use when Psi4 supports the required molecular method/property in the available
project environment and its input/output can be retained and validated.

## Do not use when

Do not silently rely on defaults for geometry units, reference wavefunction,
symmetry, frozen core, convergence, grids, memory, or basis. A normal
termination does not establish state or method validity.

## Required inputs

Source geometry, units, charge/multiplicity, fragments, method/basis, reference,
target energy/property/gradient, options, memory/threads, and convergence and
comparison plan.

## Minimum capability probe

Run a small representative input, record version and resolved options, verify
electron count/state and units, inspect convergence and output variables, and
reproduce one value from the primary output.

## Evidence and validation

Retain input, output, version/environment, basis information, settings, logs,
wavefunction/restart artifacts where relevant, failed alternatives, and
convergence or method scans. Compare equivalent states and observables only.

## Output contract

Return the computed value and reference, exact model chemistry, convergence and
sensitivity, warnings, primary artifacts, and the maximum defensible computed
claim.

## Stop or replan

Stop on unresolved charge/spin/fragments, unsupported method/property,
nonconvergence, inconsistent units/reference states, or sensitivity larger than
the claimed effect.

## Official references

- https://psicode.org/
- https://psicode.org/psi4manual/master/
