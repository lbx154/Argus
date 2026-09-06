---
name: "Medical Evidence Review"
description: "Use when independently reviewing biomedical evidence, target-disease dossiers, clinical-trial comparisons, safety claims, failures, and research-only delivery boundaries."
---

Inspect source IDs, canonical URLs, exact queries, raw responses, normalized
records, and every claim-critical locator. Metadata is not full-text review;
registration is not efficacy; preclinical activity is not clinical benefit;
association is not mechanism; absence from a bounded search is not proof of
absence.

Check target and disease identity, population or model, intervention,
comparator, endpoint, sample size, units, phase, status, dates, follow-up, data
cutoff, and whether comparisons are like-for-like. Retain contradictory, null,
negative, failed-program, and infrastructure evidence separately. Reject
invented effect values and unsupported causal or safety conclusions.

Certify only a non-diagnostic research dossier whose claims trace to inspected
sources and whose uncertainty and missing evidence remain visible. Reject
patient-specific diagnosis, treatment selection, or claims that Argus created
or clinically validated a new drug.

For a bounded request, review in one pass: read it once, verify all cited
source IDs in one batched request, and check it against the request once. Do not
repeat passing checks or inspect dossier files the conclusion does not rest on.
