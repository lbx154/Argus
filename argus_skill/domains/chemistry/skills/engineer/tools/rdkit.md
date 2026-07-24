---
name: RDKit Molecular Integrity
description: Parse, canonicalize, validate, fingerprint, and compute molecular descriptors with RDKit when a chemistry task depends on exact molecular identity.
category: chemistry-tool-rdkit
version: 1
---

Use RDKit for deterministic molecular structure handling instead of asking a
language model to emulate chemistry rules.

Setup in the project environment; prefer the current official conda-forge package:
`conda install -c conda-forge rdkit`. Verify the current release before pinning.

Minimum probe:

```python
from rdkit import Chem, rdBase
mol = Chem.MolFromSmiles("CCO")
assert mol is not None
print(rdBase.rdkitVersion, Chem.MolToSmiles(mol, canonical=True))
```

Record the input string, canonical SMILES, identifiers, sanitization failures,
stereochemistry, protonation/tautomer assumptions, descriptor/fingerprint
parameters, and RDKit version. A parsable molecule is not evidence of activity,
synthesizability, or assay validity.
