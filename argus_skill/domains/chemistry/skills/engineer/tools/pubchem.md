---
name: PubChem PUG REST Retrieval
description: Retrieve public compound records from PubChem PUG REST using stable identifiers and dated raw responses.
category: chemistry-tool-pubchem
version: 1
---

Use the official PUG REST API:
<https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest>. Resolve names to CIDs once,
then perform evidence-bearing queries by CID or InChIKey.

Minimal probe:

```bash
curl -fsSL 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/aspirin/property/CanonicalSMILES,InChIKey/JSON'
```

Retain URL, query date, HTTP status, raw response, resolved CID, InChIKey, and
requested fields. Respect service limits and cache immutable raw responses.
PubChem records aggregate submitted data; a database field is not a new
measurement and may contain source-specific conflicts.
