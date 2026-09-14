---
name: "PySCF Quantum Chemistry"
description: "Use PySCF as a Python electronic-structure capability for molecular or periodic calculations after charge, spin, geometry, method, basis, boundary, and convergence requirements are defined."
---

## When to use

Use when an existing project environment provides PySCF and a reproducible
Python workflow is appropriate for energies, orbitals, gradients, response,
correlated methods, embedding, or periodic calculations.

## Do not use when

Do not choose PySCF merely because it is scriptable. Do not accept SCF
convergence as the correct state or method accuracy, and do not present computed
results as measurements.

## Required inputs

Original structure/cell, units, charge/spin or magnetic state, basis and ECP,
method/reference, symmetry, periodic settings, grids, thresholds, memory,
parallelism, target observable, and convergence plan.

## Minimum capability probe

Run a small representative calculation, print the PySCF/version and resolved
input, verify electron count and spin, inspect convergence and primary output,
and confirm restart/checkpoint and parser behavior before scaling.

## Evidence and validation

Retain the exact script/input, environment/version, structures, basis/ECP source,
settings, logs, checkpoints, raw results, warnings, convergence scans, failed
states, and analysis. Test alternative states and method/numerical sensitivity
according to the computational chemistry workflow.

## Output contract

Return the computed observable with units/reference state, method and model,
convergence evidence, uncertainty/sensitivity, primary artifacts, and a
`computed` evidence label.

## Stop or replan

Stop when the requested feature is unavailable or unvalidated in the installed
version, state identity is unresolved, convergence is false or unstable, or
credible method alternatives change the conclusion.

## Official references

- https://pyscf.org/
- https://pyscf.org/user.html
