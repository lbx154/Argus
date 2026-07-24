---
name: GuacaMol Molecular Design Benchmarking
description: Run GuacaMol distribution-learning or goal-directed benchmarks with official tasks, scoring functions, and comparable baselines.
category: chemistry-tool-guacamol
version: 1
---

Use <https://github.com/BenevolentAI/guacamol_baselines>. Verify package age,
Python compatibility, task definitions, and baseline reproducibility before
adopting it.

Retain repository commit, environment, benchmark suite and version, training
data provenance, generator/optimizer configuration, seeds, raw generated
molecules, validity/uniqueness/novelty records, score outputs, timing, and
failures.

Do not tune against hidden benchmark answers or report one task as broad
molecular-design superiority. Check chemistry filters and unrealistic score
exploitation rather than trusting an aggregate objective alone.
