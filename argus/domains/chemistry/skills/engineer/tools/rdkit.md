---
name: "RDKit Molecular Integrity"
description: "Use RDKit for deterministic molecular parsing, sanitization, stereochemistry, canonical identifiers, substructure, descriptors, fingerprints, conformers, or reaction objects; not for proving chemical identity or feasibility."
---

## When to use

Use after the molecular identity requirements are known and a Python
cheminformatics capability is needed for structures or reactions.

## Do not use when

Do not let successful sanitization prove the source identity, purity,
protonation, tautomer, stereochemistry, conformation, activity, or synthetic
feasibility. Do not silently strip salts or select the largest fragment.

## Required inputs

Preserve source representation, component policy, stereochemistry, isotope and
charge state, aromaticity/kekulization expectations, target RDKit version, and
the exact operation.

## Minimum capability probe

Parse one representative difficult input, inspect warnings, atom count,
components, charge and stereochemistry, serialize it, parse again, and compare
identity. Probe the exact descriptor, fingerprint, reaction, or conformer feature
before batch use.

## Evidence and validation

Retain original and derived representations, version, sanitization flags,
canonicalization/stereo options, errors, and atom mappings where relevant.
Cross-check claim-critical conversions with an independent representation or
parser. Fingerprint similarity is representation- and parameter-dependent;
descriptor values require definitions and units.

## Output contract

Return the operation, version/options, successful and failed records, identity
changes, warnings, and the evidence limitation. Keep invalid molecules visible.

## Stop or replan

Stop when sanitization requires an unapproved chemistry change, stereochemistry
or components are lost, or the requested chemical concept is not represented by
the chosen RDKit operation.

## Official references

- https://www.rdkit.org/docs/
- https://github.com/rdkit/rdkit/releases
