---
name: target-disease-research
description: Use when researching a biomedical target and disease relationship, mechanism, human evidence, clinical translation, safety, failed programs, competitive trials, or an auditable pharmaceutical decision dossier.
---

# Target-Disease Research

This workflow supports pharmaceutical research and portfolio decisions. It does
not diagnose a patient, select treatment, or assert that a new drug was created.

1. Require a target and disease. Capture aliases, population or subtype, date
   bounds, intervention class, exclusions, decision question, and output
   language when supplied.
2. Stop if the request contains patient-identifying information and ask for a
   de-identified research question.
3. Reuse only the project-resolution part of `argus-run`: call
   `argus_project_list` for the exact work directory, then reuse that project or
   call `argus_project_create`. Do not dispatch while resolving the project.
4. Call `argus_message` exactly once with an objective that asks Manager to
   select the `medical` vertical (shipped by the `argus-verticals` package)
   and to produce:
   `medical/evidence.jsonl`, `medical/evidence_matrix.csv`,
   `medical/target_disease_memo.md`, and `medical/review.json`.
5. Require source IDs and URLs, exact retrieval/query provenance, conflicts,
   failures, missing fields, and a separate infrastructure-failure count.
6. Treat PubMed metadata as metadata, not full-text verification. Treat trial
   registration as trial existence/design, not evidence of efficacy. Qualify
   cross-trial comparisons unless population, endpoint, comparator, follow-up,
   treatment line, and data cutoff align.
7. Return the project ID. Use `argus-status` for later progress or artifact
   inspection instead of polling in the same turn.

Only describe a dossier as reviewed when Argus returns a Reviewer-certified
state. Preserve negative and contradictory evidence.
