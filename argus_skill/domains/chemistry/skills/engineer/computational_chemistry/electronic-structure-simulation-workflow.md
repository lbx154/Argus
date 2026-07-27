---
name: Reproducible Electronic-Structure and Molecular Simulation Workflow
description: Run reproducible molecular or periodic quantum, atomistic, or free-energy calculations with method and numerical convergence separated; excludes battery cycling, peak fitting, and assay interpretation.
category: chemistry-computational
version: 1
---

## When to use
Use for a bounded, executable calculation of an electronic, thermochemical,
spectroscopic, structural, kinetic, or statistical-mechanical observable.

## Do not use when
Do not use a successful run to substitute for a measurement, to interpret unprocessed
instrument output, or to select a battery protocol or protein assay.

## Scientific question
Within a declared Hamiltonian/model and conditions, what computed or simulated
observable supports or challenges the question?

## Required inputs
Use an identity-normalized system, target observable and tolerance, method candidates,
environment/ensemble, initial geometries or seeds, reference data if available, and
compute constraints.

## Identity and normalization
Reconfirm charge/spin, coordinates/cell, composition, temperature/pressure,
solvent/electrolyte representation, boundary conditions, and units. Label inputs as
retrieved, measured, generated, or assumed.

## Decision procedure
Choose molecular versus periodic treatment from the physical system; choose a
validated method family appropriate to correlation, dispersion, excited state,
relativity, charged defects, or long-timescale sampling. For simulations, define
ensemble, equilibration criterion, independent replicas, sampling window, and
observable estimator. For energy differences, use consistent levels and state
definitions. Treat numerical convergence (basis/cutoff/k-points/SCF/timestep/length)
separately from method adequacy.

## Tool-selection ladder
Inspect inputs and references; run a tiny native-engine calculation; use established
electronic-structure, molecular-simulation, or analysis capabilities available in the
project; use higher-level or alternate methods only to resolve claim-critical
methodological uncertainty. ORCA, Psi4, PySCF, and MD engines are possible
capabilities, not required entrypoints.

## Minimum capability probe
Run a minimal single-point, short dynamics segment, or small periodic calculation that
writes a parseable primary output. Confirm version, requested method, charge/spin,
units, and restart behavior before scaling.

## Evidence to retain
Original/normalized inputs, exact input decks, engine/version, hardware-relevant
settings, seeds, logs, trajectories/checkpoints where applicable, raw output,
convergence scans, analysis code, failed jobs, and external reference provenance.

## Validation gates
Require stable completion and parser agreement with primary output; numerical
convergence against a predeclared tolerance; physically plausible state/trajectory;
and method sensitivity adequate for the requested claim. Execution success alone
passes none of these gates.

## Common failure modes
SCF convergence mistaken for a correct state; one optimized conformer reported as an
ensemble; insufficient sampling; inconsistent standard states; finite-size artifacts;
and pseudo-converged free energies from correlated frames.

## Uncertainty and applicability domain
Report computed or simulated values with numerical, sampling, and method uncertainty
separately. Do not present a calculation as measured validation; state model limits
and untested alternative states.

## Safety and authorization
Respect compute allocation, licenses, and data authorization. Computational evidence
does not authorize laboratory execution.

## Output contract
Return the scientific question, method/model and conditions, primary computed outputs,
convergence and sensitivity results, negative outcomes, uncertainty, and an honest
evidence label: computed or simulated, never measured.

## Stop, block, or replan conditions
Stop or replan for unresolved state identity, nonconvergent claim-critical results,
sampling below the observable's correlation time, or method disagreement that changes
the conclusion.

## Official references
- [NIST Computational Chemistry Comparison and Benchmark Database](https://cccbdb.nist.gov/)
- [IUPAC Green Book](https://iupac.org/what-we-do/books/greenbook/)
