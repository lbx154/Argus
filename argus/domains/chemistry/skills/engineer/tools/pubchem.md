---
name: "PubChem PUG REST Retrieval"
description: "Retrieve public compound, substance, assay, property, and safety records through PubChem interfaces using resolved identifiers, dated queries, raw responses, and source-aware evidence limits."
---

## When to use

Use for public compound identifiers, depositor-linked substance records,
standardized properties, assay metadata, synonyms, or Laboratory Chemical Safety
Summaries.

## Do not use when

Do not treat a name search as unique identity, a computed property as measured,
a depositor record as independently curated truth, or a PubChem entry as proof
of availability, purity, safety for a procedure, or a new observation.

## Required inputs

Resolved query identity, endpoint/fields, record type, source expectations,
access date, rate/size constraints, and downstream evidence need.

## Minimum capability probe

Resolve one known identifier, fetch the exact fields, save the raw response, and
verify CID/SID/AID semantics, compound identity, units, value provenance, and
whether each property is experimental or computed.

## Evidence and validation

Retain request URL or parameters, response, access date, identifiers, record
sources, units, and conflicts. Prefer stable identifiers over names and inspect
linked primary sources for claim-critical values.

## Output contract

Return resolved identity, requested records, provenance, evidence class,
conflicts, and fields unavailable from the selected endpoint.

## Stop or replan

Stop on ambiguous identity, incompatible records, undocumented units/methods, or
an API result that cannot support the requested claim.

## Official references

- https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
- https://pubchem.ncbi.nlm.nih.gov/docs
