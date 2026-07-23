---
name: DeepChem Molecular Machine Learning
description: Build molecular ML datasets, featurizers, splits, models, and evaluations with DeepChem under explicit contamination controls.
category: chemistry-tool-deepchem
version: 1
---

Use <https://github.com/deepchem/deepchem>. Verify the current release and
backend compatibility before installing into the project environment.

Probe the exact featurizer, dataset loader, split, and model family needed by the
experiment, not merely `import deepchem`. Record package/backend versions,
dataset provenance, canonicalization, featurizer parameters, split indices,
seeds, hyperparameters, checkpoints, raw predictions, failures, and metrics.

Random splits are often optimistic for chemistry. Use scaffold, temporal, system,
or task splits when the claim requires extrapolation, and keep test labels out of
model and agent decisions.
