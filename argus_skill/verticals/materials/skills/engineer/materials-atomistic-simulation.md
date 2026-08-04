---
name: "Materials Atomistic Simulation"
description: "Run provenance-preserving DFT, ML interatomic-potential, molecular-dynamics, phonon, defect, diffusion, and stability studies with scale-appropriate convergence and validation."
---

# Materials Atomistic Simulation

## Route selection

- Use electronic-structure calculations when the claim depends on bonding,
  charge, magnetic or electronic state, reaction energetics, or reference labels.
- Use classical or ML interatomic potentials for larger systems and longer
  trajectories only after checking that the potential covers the relevant
  elements, phases, configurations, energies, forces, and stresses.
- Use an installed Quantum ESPRESSO, ABACUS, or licensed VASP for DFT only after
  inspecting its local version, pseudopotentials, and project execution path.
- Use atomate2/jobflow or AiiDA for durable multi-job workflows when available.
  AtomisticSkills provides reusable tool/skill patterns; MatClaw demonstrates a
  code-first HPC route. Argus remains responsible for planning and review.

## Execution contract

1. Preserve the exact initial structure, cell, species, occupancies, charge,
   magnetic initialization, and any defects or interfaces.
2. Record code/version, pseudopotential or basis identity, exchange-correlation
   functional, dispersion treatment, cutoff, k-point mesh, smearing, electronic
   and ionic tolerances, spin settings, and hardware.
3. For MD, record the potential/model revision, ensemble, thermostat/barostat,
   time step, equilibration, production length, seeds, sampling interval, and
   initial-condition generation.
4. Store native outputs and parse them without discarding warnings or failed
   electronic/ionic steps.
5. Check convergence of every parameter capable of changing the conclusion.
   Relaxation convergence alone is not property convergence.
6. Check cell-size, sampling-time, finite-temperature, and finite-size effects
   where they matter. Report statistical uncertainty from independent samples or
   defensible correlation analysis.
7. Validate against analytic limits, higher-fidelity calculations, trusted
   implementations, OpenKIM tests, public benchmark data, or matched experiments.

## Automatic claim downgrades

- A potential outside its training/validation domain supports an exploratory
  result, not a reliable material prediction.
- A short stable trajectory is not evidence of long-time thermodynamic stability.
- A 0 K perfect-crystal result is not a room-temperature processed-material result.
- Formation energy alone does not establish synthesizability.
- Agreement between two tools sharing the same model assumptions is not fully
  independent validation.
