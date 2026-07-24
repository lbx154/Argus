---
name: Psi4 Quantum Chemistry
description: Run Psi4 electronic-structure calculations with explicit molecule, method, basis, options, and convergence evidence.
category: chemistry-tool-psi4
version: 1
---

Use <https://psicode.org/> and current official installation guidance, commonly
through conda-forge. A valid probe imports `psi4`, prints its version, and runs a
small documented energy calculation in the intended execution mode.

Keep the complete input, geometry and units, charge/multiplicity, method,
basis/ECP, frozen-core and convergence options, memory/thread settings, output,
wavefunction/checkpoint artifacts, version, and failures.

Do not compare energies produced with inconsistent geometries, reference states,
methods, bases, or unit conventions. A clean exit is not a scientific accuracy
certificate.
