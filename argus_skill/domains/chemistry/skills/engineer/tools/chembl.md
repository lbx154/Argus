---
name: ChEMBL Bioactivity Retrieval
description: Query ChEMBL compounds, targets, assays, and activities with explicit assay and unit provenance.
category: chemistry-tool-chembl
version: 1
---

Use the official client or REST service:
<https://github.com/chembl/chembl_webresource_client>.
Install in the project environment with
`pip install chembl_webresource_client` after verifying the current release.

Probe:

```python
from chembl_webresource_client.new_client import new_client
print(new_client.molecule.get("CHEMBL25")["molecule_chembl_id"])
```

Retain ChEMBL release, molecule/target/assay identifiers, filters, units,
relations, confidence scores, raw records, and query date. Do not merge activity
values across assay types, organisms, endpoints, or units without an explicit
scientific rule.
