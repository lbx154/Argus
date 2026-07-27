---
name: Open Babel Structure Conversion
description: Use Open Babel for chemical format conversion, independent parsing, hydrogen or coordinate operations, and interoperability checks while preserving source identity and conversion provenance.
category: chemistry-tool-conversion
version: 2
---

## When to use

Use when project tools require another molecular file format or when a second
parser can expose representation loss.

## Do not use when

Do not use format conversion as a scientific repair. File formats differ in
bond order, aromaticity, charge, stereochemistry, periodicity, residue, and
metadata support; a successful write does not prove semantic equivalence.

## Required inputs

Original file, format/version, expected components and chemistry, target format,
coordinate dimensionality, hydrogen policy, aromaticity/bond-order policy, and
the downstream consumer.

## Minimum capability probe

Convert one representative record, capture diagnostics, parse source and output
independently, and compare composition, charge, stereochemistry, components,
coordinates/cell, and metadata required downstream.

## Evidence and validation

Retain the original, command or API options, Open Babel version, output, logs,
and a field-by-field loss report. Never overwrite source data. Treat generated
3D coordinates, hydrogens, charges, or bond orders as derived assumptions.

## Output contract

Return converted files plus explicit preserved, transformed, lost, and inferred
fields. State whether conversion is fit for lookup, display, modeling, or only
manual inspection.

## Stop or replan

Stop when the target format cannot preserve claim-critical chemistry or when two
parsers disagree on identity.

## Official references

- https://openbabel.org/docs/
- https://github.com/openbabel/openbabel/releases
