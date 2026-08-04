---
name: "Open Reaction Database I/O"
description: "Read, validate, query, and write Open Reaction Database records while preserving reaction roles, quantities, conditions, outcomes, provenance, and schema validation; not as proof of reproducibility."
---

## When to use

Use when structured reaction records are available in ORD format or when a
project needs schema-valid exchange of source-linked reaction data.

## Do not use when

Do not fabricate missing experimental fields to satisfy the schema. Schema
validity does not establish balanced chemistry, correct extraction, reliable
yield, or reproducible synthesis.

## Required inputs

ORD schema/package version, source records, reaction identity policy, component
roles, quantities/units, conditions, workup/outcome data, provenance, and the
intended read/query/write operation.

## Minimum capability probe

Parse and validate one representative record, inspect warnings, recover
reactants/reagents/products, conditions and outcomes, serialize it, parse again,
and compare all claim-critical fields.

## Evidence and validation

Retain original serialized records, schema version, validation output,
transformations, source links, missingness, and conflicts. Check identities,
stoichiometry, yield basis, stereochemistry, and procedure completeness with the
organic reaction-data workflow.

## Output contract

Return schema-valid records plus a scientific validation report distinguishing
source fields, normalized fields, inferred fields, and unresolved omissions.

## Stop or replan

Stop when normalization changes reaction meaning, source provenance is absent,
or schema fields cannot represent claim-critical information without loss.

## Official references

- https://open-reaction-database.org/
- https://docs.open-reaction-database.org/
