---
name: "AiZynthFinder Retrosynthesis"
description: "Run a local AiZynthFinder retrosynthesis search with explicit target, model, stock, configuration, search budget, route output, and evidence limits; use for candidate generation, not feasibility proof."
---

## When to use

Use after target identity and route objectives are defined and an available local
AiZynthFinder installation can generate candidate retrosynthetic trees.

## Do not use when

Do not assume default models/stocks exist, are licensed for the use, cover the
target chemistry, or represent current purchasability. A solved search tree is
not an experimentally feasible route.

## Required inputs

Target representation, package/version, expansion and filter policies, stock,
configuration, search algorithm/budget, route scorers, hardware limits, and
output format.

## Minimum capability probe

Load the exact model and stock, run a known target, verify stereochemistry and
components, inspect one route and source/stock membership, and confirm failed or
unsolved outputs are captured before a campaign.

## Evidence and validation

Retain target, configuration, model/stock identifiers and provenance, seeds,
budget, logs, routes, scores, unsolved targets, and version. Review every
claim-critical step for precedent, selectivity, conditions, availability,
safety, and whole-route compatibility.

## Output contract

Return candidate routes, search provenance, stock assumptions, route scores,
failure states, and an explicit `generated-not-validated` evidence label.

## Stop or replan

Stop when model/stock provenance is unknown, target identity is altered, search
failure is hidden, or the route cannot be inspected outside the score.

## Official references

- https://molecularai.github.io/aizynthfinder/
- https://github.com/MolecularAI/aizynthfinder/releases
