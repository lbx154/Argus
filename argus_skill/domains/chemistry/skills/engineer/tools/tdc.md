---
name: Therapeutics Data Commons Benchmarking
description: Use TDC datasets, splits, evaluators, and predictive oracles with exact task and evidence-boundary provenance.
category: chemistry-tool-tdc
version: 1
---

Use <https://github.com/mims-harvard/TDC> and
<https://tdcommons.ai/>. Verify the current `PyTDC` release, dataset page,
oracle documentation, license/access terms, and cache contents before running.

Probe the exact dataset or oracle in a project-local environment. Retain package
version, task/dataset/oracle name, download source/date, split, preprocessing,
cache and code hashes, seeds, raw trajectories, evaluator I/O, and failures.

TDC predictive-oracle outputs are model predictions, not wet-lab measurements.
For agent benchmarks, expose only queried values, declare the evaluator threat
model, and compare online, frozen, and conventional policies under equal budgets
without merging those labels.
