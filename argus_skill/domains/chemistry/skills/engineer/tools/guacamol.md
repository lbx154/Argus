---
name: GuacaMol Molecular Design Benchmarking
description: Run documented GuacaMol distribution-learning or goal-directed benchmarks with exact tasks, scoring functions, reference data, budgets, baselines, validity checks, and benchmark-only claim limits.
category: chemistry-tool-benchmark
version: 2
---

## When to use

Use when the research question explicitly concerns GuacaMol-compatible molecular
generation benchmark performance or reproducibility.

## Do not use when

Do not equate benchmark score with synthesizability, novelty beyond the declared
corpus, biological activity, safety, or experimental discovery. Do not modify
scoring functions after inspecting outcomes.

## Required inputs

Benchmark implementation/version, task set, reference data/version, molecular
standardization, scoring functions, validity/uniqueness/novelty definitions,
query/sample budget, seeds, baselines, and answer-access boundary.

## Minimum capability probe

Score known molecules and invalid inputs, reproduce one documented baseline or
metric, verify canonicalization and duplicates, and confirm the generator cannot
read hidden targets beyond the intended score interface.

## Evidence and validation

Retain configurations, reference data, generated molecules, invalid/duplicate
records, score components, budgets, seeds, baselines, runtime failures, and
post-benchmark chemistry checks. Evaluate distribution and failure modes, not
only the best score.

## Output contract

Return benchmark/task identity, comparable scores, molecule sets, validity and
duplicate analysis, baseline comparison, and explicit limits on real-world claims.

## Stop or replan

Stop when task/scorer versions differ, evaluation answers leak, generated
identity is unstable, or the benchmark does not test the claimed chemistry.

## Official references

- https://github.com/BenevolentAI/guacamol
- https://benevolent.ai/guacamol
