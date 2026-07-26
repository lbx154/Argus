---
name: Chemistry Dataset Curation and Leakage Control
description: Build and evaluate chemical datasets with explicit provenance, deduplication, split logic, label quality, and leakage controls across molecules, reactions, materials, spectra, batteries, and biomolecular systems.
category: chemistry-foundation-data
version: 1
---

## When to use

Use when constructing, merging, cleaning, splitting, benchmarking, or training
on chemistry data, including literature extraction and public database exports.

## Do not use when

Do not apply one molecular scaffold-split recipe to reaction, crystal, battery,
spectral, or biochemical data. Choose grouping variables from the process that
generated dependence between records.

## Required inputs

- Dataset purpose, target definition, unit, population, and decision use.
- Source records, licenses, versions, access dates, and extraction queries.
- Entity and sample keys before and after normalization.
- Label-generation method and uncertainty.
- Known grouping structure: publication, patent, batch, laboratory, instrument,
  molecule/scaffold, reaction family, crystal prototype, material system,
  battery cell, subject, protein family, or time.

## Curation workflow

1. Freeze raw source exports and record provenance.
2. Normalize identity without deleting source fields.
3. Detect exact and chemically meaningful near duplicates.
4. Resolve conflicts by rule; otherwise retain conflict labels and provenance.
5. Audit missingness, censoring, class balance, coverage, and conditional bias.
6. Define train/validation/test splits before model selection.
7. Group records that share upstream information or future knowledge.
8. Fit preprocessing only on training data.
9. Keep a locked test set or external benchmark when the claim requires it.
10. Produce dataset cards describing inclusions, exclusions, limitations, and
    intended use.

## Leakage tests

- Duplicate or normalized-equivalent entities across splits.
- Same publication, patent, batch, cell, specimen, trajectory, or instrument run
  across splits.
- Future cycles, later measurements, or post-outcome features used to predict
  earlier outcomes.
- Labels or benchmark answers present in features, filenames, metadata, caches,
  prompts, or retrieval corpora.
- Hyperparameter selection or manual curation informed by the final test set.
- Multiple databases that reproduce the same upstream measurement.

## Validation gates

Report performance for a scientifically appropriate split and a simple strong
baseline. Include group sizes, exclusions, duplicate rates, label conflicts,
coverage, seeds, and confidence intervals or repeated splits where appropriate.
Do not claim prospective performance from a random retrospective split.

## Output contract

Return source inventory, schema, identity keys, curation rules, exclusion counts,
split rationale, leakage audit, dataset limitations, and reproducible split
artifacts or code.

## Stop, block, or replan conditions

Block model claims when source provenance or grouping cannot be reconstructed,
when the test set was exposed during development, or when label semantics differ
across merged sources without a defensible harmonization.

## Official references

- Open Reaction Database schema: https://docs.open-reaction-database.org/
- OPTIMADE specification: https://www.optimade.org/optimade/
- NIST Materials Data Repository: https://materialsdata.nist.gov/
- UniProt data documentation: https://www.uniprot.org/help/
