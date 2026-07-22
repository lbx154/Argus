---
name: Materials Simulation Sign-off
description: Audit atomistic, continuum, CAD/CAE, and process simulations from native inputs and outputs, including material state, discretization, solver health, convergence, and claim fidelity.
category: materials-simulation-review
version: 2
---

# Materials Simulation Sign-off

## Review protocol

1. Reconstruct what was actually simulated: material identity/state, geometry or
   cell, governing model, parameter sources, loads, boundary/initial conditions,
   interactions, numerical controls, and requested observable.
2. Inspect native inputs and outputs. Confirm solver version, job identity, exit
   status, warnings, iterations or increments, and the exact files used for
   postprocessing.
3. For electronic/atomistic work, inspect structure, pseudopotential/basis or
   force-field/model identity, convergence controls, cell/k-point/cutoff or
   sampling/time-step choices, ensemble, equilibration, and finite-size effects.
4. For continuum/process work, inspect CAD units/topology, mesh and element
   choice, contact, material law and calibration range, large-deformation or
   thermal coupling settings, distortion/remeshing/deletion, and state transfer.
5. Rerun a bounded critical check when practical. Compare extracted values with
   the native result rather than trusting a report or screenshot.
6. Require convergence and physical checks on claim-critical observables.
   Agreement at one discretization or a green solver icon is insufficient.
7. Compare against an independent result under matched conditions and inspect
   sensitivity to assumptions capable of reversing the conclusion.
8. Keep scientific integrity separate from Manager lifecycle state. Reject a
   model or execute contract that freezes the whole
   `research/PIPELINE_STATE.json` hash or spends a repair mission solely because
   the Manager advanced the stage. Scientific guards should hash immutable data,
   code, model, and protocol artifacts; lifecycle provenance should cite the
   Manager-authored stage-history transition.

## Automatic rejection conditions

- input deck, generated script, submitted job, or screenshot presented as a result;
- unconverged electronic structure, unstable MD, failed increment, excessive
  element distortion, or ignored fatal warning;
- material properties used outside their temperature/rate/phase/calibration range;
- calibration data reused as independent validation;
- missing units or mismatched geometry, loading, temperature, rate, or metric;
- stale output, manually edited result, selective deletion, or undocumented rerun;
- a false integrity failure caused only by an authorized Manager stage transition;
- commercial solver or instrument result claimed without real access and native evidence.
