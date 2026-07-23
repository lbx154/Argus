---
name: PySCF Quantum Chemistry
description: Execute reproducible molecular electronic-structure calculations with PySCF when energies, orbitals, gradients, or correlated methods are required.
category: chemistry-tool-pyscf
version: 1
---

Use <https://github.com/pyscf/pyscf>. Install the current official package in the
project environment and run a tiny calculation before committing compute.

```python
from pyscf import gto, scf
mol = gto.M(atom="H 0 0 0; H 0 0 0.74", basis="sto-3g", unit="Angstrom")
mf = scf.RHF(mol).run()
assert mf.converged
print(mf.e_tot)
```

Retain geometry, units, charge, spin, basis/ECP, method, grids, symmetry,
convergence thresholds, software version, stdout, checkpoint, and hardware.
Convergence is necessary but not sufficient: assess basis, correlation,
relativistic, solvation, and geometry errors for the claim.
