---
name: Materials CAD CAE and Process Simulation
description: Build and validate parameterized geometry, meshes, constitutive models, contacts, thermal-mechanical loads, and solver workflows for material behavior and manufacturing processes.
category: materials-cae-processing
version: 1
---

# Materials CAD, CAE, and Process Simulation

## Tool route

- For headless parameterized geometry, prefer build123d/CadQuery; use FreeCAD or
  a commercial CAD API when feature coverage or existing assemblies require it.
- Use Gmsh or the solver-native mesher and retain mesh-quality statistics.
- Open routes include CalculiX, FEniCSx, and MOOSE. Licensed routes may use
  Abaqus scripting, PyAnsys, COMSOL Java/MPh, or DEFORM's Python API.
- CAE-Agent-Hub, AbaqusAgent, and sim-cli are useful adapter and failure-recovery
  references. Do not delegate scientific judgment to them.

## Geometry and mesh

1. Parameterize dimensions that the research question may change. Preserve the
   source model and export a solid format such as STEP when appropriate; STL is
   not a parametric solid model.
2. Verify topology, units, bounding box, volume, interfaces, contact surfaces,
   and orientation before meshing.
3. Choose element type, order, integration, local refinement, contact
   discretization, and quality limits for the expected deformation, thermal
   gradients, incompressibility, fracture, or localization.
4. Perform mesh-convergence or discretization-error analysis on the observables
   used in claims, not only on element count.

## Material and process model

1. Tie every material card or user subroutine to composition, phase, temperature,
   strain rate, processing history, and calibration data. State isotropy,
   anisotropy, hardening, rate, thermal, damage, fracture, creep, and phase-change
   assumptions.
2. Keep calibration and validation cases separate. A constitutive fit reproducing
   its own calibration curve is not independent validation.
3. Define contact, friction, heat transfer, tooling, constraints, loads,
   amplitudes, boundary/initial conditions, and process sequence from the real
   setup.
4. For forming, machining, additive, heat-treatment, or joining simulations,
   check mass/energy behavior, remeshing or element deletion, distortion,
   temperature history, state transfer, and path dependence.

## Solver loop

Use bounded, inspectable steps:

```text
detect/version/license
-> create or import geometry
-> validate geometry
-> mesh and inspect quality
-> assign material/process model
-> apply interactions and BC/IC
-> lint or data-check
-> solve
-> inspect status and native diagnostics
-> extract observables
-> checkpoint artifacts
```

Classify failures as geometry, mesh, material, interaction, BC/load, convergence,
license, or postprocessing errors. Repair the implicated stage and rerun its
checks; do not blindly regenerate the whole model.

## Evidence boundary

A `.inp`, Workbench project, or DEFORM database proves only model construction.
A completed job proves only execution. Scientific acceptance additionally needs
convergence, physical sanity, sensitivity, and comparison under matched
conditions. Never report a GUI image as the sole evidence for a numerical value.
