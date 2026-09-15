---
name: "Web Source-Evidence Audit"
description: "Review whether fetched web sources actually support consequential current-world claims."
---

# Web Source-Evidence Audit

Use this when a decision or paper claim depends on current web information.
Open the cited primary source and compare its actual text, code, or observable
behavior with the claim.

Follow the exact cached source paths cited in the deliverable; inspect actual
text, not only HTTP status or Engineer's paraphrase. If a path is missing, check
the project's `.argus/sources/` once, then return the exact essential claim and
missing URL/path for repair. Do not guess filenames or search shared `/tmp`.

Check that:

- the URL resolves to the named source and the access date is clear;
- quoted text is verbatim and supports the stated scope;
- official documentation is not presented as public implementation;
- marketing remains explicitly vendor-attributed;
- an inference names its premises and does not become a fact;
- closed-source details remain unknown without technical evidence.

Return the exact unsupported wording and smallest repair through the normal
Reviewer response. Do not require a separate evidence file or audit report.
