---
name: Materials Validation Review
description: Independently validate materials claims through convergence, physical invariants, sensitivity, uncertainty, matched baselines, public benchmarks, and experimental evidence.
category: materials-validation-review
version: 1
---

# Materials Validation Review

Validation must be capable of falsifying the result. Choose checks from the
method and claim rather than requiring the same packet for every task.

## Numerical validity

- inspect mesh, time-step, cell-size, k-point, cutoff, trajectory-length,
  sampling, optimizer, and tolerance convergence where relevant;
- check conservation, symmetry, dimensional consistency, limiting cases, known
  trends, and physically admissible ranges;
- verify that postprocessing definitions match the reported observable;
- assess stochastic variability and correlation rather than reporting one seed
  or trajectory as exact.

## Model validity

- verify material composition, phase, microstructure, temperature, rate,
  environment, and processing history;
- identify extrapolation beyond constitutive, potential, surrogate, or
  measurement calibration;
- test sensitivity to assumptions and model forms capable of changing the claim;
- distinguish agreement caused by shared assumptions from independent evidence.

## External validity and integrity

- compare with an analytic result, trusted implementation, public benchmark,
  published measurement, or new experiment under matched conditions;
- keep evaluator/reference data frozen and separate from optimization;
- inspect all relevant attempts, not only the selected successful run;
- reject hard-coded outputs, edited references, target leakage, mismatched
  hardware/conditions/metrics, and unsupported cherry-picking;
- calibrate the conclusion to supported, partial, inconclusive, no-go, or unknown.

Do not demand physical experiments for a claim explicitly bounded to a
simulation method, but do require the report to say that experimental validity
remains unestablished. Conversely, do not call a simulation-only match an
experimental discovery.
