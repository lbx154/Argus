---
name: DeepChem Molecular Machine Learning
description: Use DeepChem for molecular or materials datasets, featurizers, splitters, models, and evaluation only after target semantics, identity, grouping, leakage, baseline, and applicability requirements are fixed.
category: chemistry-tool-machine-learning
version: 2
---

## When to use

Use when an existing project chooses DeepChem as an implementation capability
for a defined chemistry machine-learning study.

## Do not use when

Do not select a dataset, random split, featurizer, metric, or model because it is
the shortest example. Do not treat library convenience datasets as current,
licensed, leakage-free, or fit for the scientific claim.

## Required inputs

Data source/version/license, identity and target schema, unit/condition policy,
grouping and split design, featurizer/model candidates, baselines, metrics,
seeds, compute budget, and intended applicability domain.

## Minimum capability probe

Load a small source-controlled subset, verify identities/labels/units, apply the
intended split without overlap, train a simple baseline, serialize/reload the
pipeline, and reproduce one prediction and metric.

## Evidence and validation

Retain raw/curated data lineage, exclusions, duplicate and leakage audit, split
membership, preprocessing fit scope, package/dependency versions, configuration,
seeds, checkpoints, predictions, metrics, uncertainty, and failed runs. Compare
to simple and domain-appropriate baselines.

## Output contract

Return dataset/split/model provenance, baseline and held-out performance,
uncertainty/calibration, domain limits, errors, and `predicted` claim wording.

## Stop or replan

Stop when target semantics or licenses are unclear, split leakage persists,
evaluation lacks a meaningful baseline, or deployment inputs lie outside the
demonstrated domain.

## Official references

- https://deepchem.io/
- https://deepchem.readthedocs.io/
