---
name: Open Babel Structure Conversion
description: Convert and inspect chemical file formats with Open Babel when interoperability or independent structure parsing is required.
category: chemistry-tool-openbabel
version: 1
---

Use Open Babel for format conversion, identifier generation, and an independent
structure parser. Prefer conda-forge (`conda install -c conda-forge openbabel`);
verify current official packaging rather than assuming a PyPI wheel exists.

Probe both the CLI and Python binding when the workflow needs them:

```bash
obabel -:"CCO" -ocan
python -c "from openbabel import openbabel; print(openbabel.OBReleaseVersion())"
```

Retain the source file, declared input/output formats, command, stderr, version,
and converted output. Compare atom count, bond order, stereochemistry, charge,
and coordinates after conversion. Successful conversion does not prove chemical
equivalence when the source format omitted those properties.
