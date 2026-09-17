---
name: "Therapeutics Data Commons Benchmarking"
description: "Use TDC datasets, splits, evaluators, and oracles with exact task, version, license, identity, metric, contamination, and evidence-boundary provenance."
---

## When to use

Use when a project explicitly targets a documented TDC task or needs a TDC
dataset/evaluator/oracle as one reproducible benchmark component.

## Do not use when

Do not treat a benchmark oracle as a prospective experiment, a dataset label as
new evidence, or a leaderboard metric as transferable to another population.
Do not expose test labels or oracle answers to proposal logic.

## Required inputs

TDC/package and dataset version, task/group, entity and target definitions,
license/terms, official split/evaluator, oracle semantics, query budget,
baselines, and contamination policy.

## Minimum capability probe

Load a small sample, verify schema/identities/units, reproduce the documented
split and one metric or oracle call, and confirm caching, errors, and answer
access boundaries.

## Evidence and validation

Retain dataset metadata, raw version/source, splits, queries, oracle responses,
versions, seeds, baselines, predictions, and contamination audit. Check overlap
with pretraining, public labels, related entities, and future observations as
far as the claim requires.

## Output contract

Return the exact task, data/evaluator/oracle provenance, budget, leakage controls,
baseline comparison, uncertainty, and a benchmark-only evidence ceiling.

## Stop or replan

Stop when the benchmark version or license is unclear, official semantics cannot
be reproduced, hidden answers are exposed, or the task is a poor proxy for the
scientific objective.

## Official references

- https://tdcommons.ai/
- https://tdcommons.ai/get-started/
