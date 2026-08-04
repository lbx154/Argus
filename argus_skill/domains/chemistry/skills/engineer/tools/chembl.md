---
name: "ChEMBL Bioactivity Retrieval"
description: "Retrieve ChEMBL molecule, target, assay, document, and activity records with release, construct/target, assay, relation, unit, and provenance controls; not as direct evidence of new biological activity."
---

## When to use

Use for curated public bioactivity data, target or assay context, chemical series
research, dataset construction, or literature-linked activity evidence.

## Do not use when

Do not merge IC50, Ki, Kd, percent inhibition, cellular potency, target
engagement, and functional readouts as one endpoint. Do not treat standardized
values as comparable without assay and target context.

## Required inputs

ChEMBL release, molecule identity policy, target/organism/construct scope, assay
types, endpoint/relation/unit rules, confidence thresholds, document links, and
duplicate/grouping policy.

## Minimum capability probe

Trace one activity from molecule and target through assay and document. Compare
reported and standardized relation/value/unit, inspect flags and confidence, and
recover the primary source.

## Evidence and validation

Retain release, query, raw records, molecule/target/assay/document identifiers,
standardization fields, units, relations, flags, and source. Group related
measurements by publication, assay, target, and chemical series to prevent
leakage. Conflicting measurements remain separate.

## Output contract

Return a source-linked activity table, inclusion/exclusion rules, endpoint
definitions, identity conflicts, comparability limits, and evidence ceiling.

## Stop or replan

Stop when target or assay semantics are too broad, primary provenance is
missing, or the available records do not measure the requested biological claim.

## Official references

- https://www.ebi.ac.uk/chembl/
- https://chembl.gitbook.io/chembl-interface-documentation/
