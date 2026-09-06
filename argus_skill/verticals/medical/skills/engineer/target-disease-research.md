---
name: "Target-Disease Evidence Research"
description: "Use when building a target-disease evidence dossier — every claim traceable to its source — for a biomedical target and disease relationship, translation, safety, failures, or competitive trials."
---

## Required inputs

Record the submitted target and disease, aliases, population or subtype,
intervention class, date bounds, exclusions, decision question, and output
language. Stop on patient-identifying information. Resolve gene, protein,
pathway, drug, biomarker, indication, and subtype ambiguity explicitly.

## Evidence workflow

Match the workflow to what was asked for. For a bounded summary, stop
when each requested evidence item is supported: batch PubMed IDs into one
E-utilities request, batch trial IDs where the API permits, and do not run
overlapping searches after the evidence limit is met. Produce only the named
output; the full dossier below is for requests that actually ask for one.

1. Query PubMed and ClinicalTrials.gov with preserved exact requests and UTC
   retrieval times. Use additional sources only when available and authorized.
2. Retain raw responses before normalization. Record provider, transport,
   parse, rate-limit, resource, and interruption failures separately.
3. Normalize source IDs, canonical URLs, dates, statuses, population or model,
   intervention, comparator, endpoint, sample size, follow-up, and data cutoff
   only when the source provides them.
4. Separate mechanism, human genetics, preclinical, clinical, safety, failure,
   and contradictory evidence. Metadata is discovery evidence, not full text.
5. Qualify cross-trial comparisons unless population, subtype, biomarker,
   treatment line, comparator, endpoint, follow-up, and cutoff align.

## Deterministic tool

Use the bundled builder instead of rewriting the retrieval or dossier code. Quote
the operator's actual target and disease values:

```bash
python -m argus_skill.verticals.medical.dossier \
  --project-root . \
  --target 'EGFR' \
  --disease 'non-small cell lung cancer' \
  --live
```

For offline reproduction, replace `--live` with `--pubmed-fixture <path>` and
`--clinical-trials-fixture <path>`. Keep the resulting raw files and query
history. Do not rerun an unchanged successful retrieval.

## Output contract

For a full target-disease dossier, produce and preserve:

- `medical/scope.json`
- `medical/queries.jsonl`
- `medical/raw/pubmed/` and `medical/raw/clinical_trials/`
- `medical/evidence.jsonl`
- `medical/evidence_matrix.csv`
- `medical/target_disease_memo.md`
- `medical/review.json`

The memo must state that it supports research decisions and is not diagnosis or
treatment advice. Keep source IDs/URLs, conflicts, missingness, failures, and
claim limitations adjacent to the conclusions they bound.

## Stop or block

Block when target or disease identity is unresolved in a claim-critical way,
central evidence lacks an inspectable source, a requested conclusion needs
unavailable full text, source failures leave a material evidence stratum empty,
or the request crosses into patient care. Do not convert missing data into a
negative biomedical result.

## Official sources

- PubMed E-utilities: https://www.ncbi.nlm.nih.gov/books/NBK25501/
- ClinicalTrials.gov API: https://clinicaltrials.gov/data-api/api
- NCBI usage guidance: https://www.ncbi.nlm.nih.gov/books/NBK25497/
