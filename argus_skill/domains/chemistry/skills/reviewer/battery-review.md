---
name: "Battery and Electrochemistry Evidence Review"
description: "Independently review cell identity, protocol comparability, unit normalization, cycling/degradation evidence, and leakage-safe battery models; excludes molecular computation, general characterization, and biochemical review."
---

Inspect native cycler/potentiostat records, cell and protocol metadata, normalization
code, exclusions, cell-level results, controls, and model outputs. Reject analysis that
mixes active-mass, area, volume, half/full-cell, voltage-window, temperature, or
formation conditions without explicit justified adjustment.

Require time- and group-safe splits: later cycles, repeat observations, postmortem
data, and same-cell rows cannot leak into earlier or held-out predictions. Check
capacity, efficiency, resistance, and SOH formulas against raw units and native
channels. Distinguish measured electrochemistry from derived diagnostics, model fits,
simulations, and predictions.

Reject mechanism or safety claims based only on cycling curves/model parameters;
require suitable controls and orthogonal evidence. Replan for missing raw data,
protocol ambiguity, non-independent replicates, no baseline, or uncertainty larger
than the claimed effect. Certify only the observed cell/protocol domain and retain
negative results.
