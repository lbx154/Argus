---
name: "Optimizing for an external target"
description: "How the Planner prioritizes work when an external scorer or the operator's success criterion is the target."
---

# Optimizing for an external target

- The operator's success criterion or external scorer outranks the public or
  reference baseline, the current local incumbent and secondary metrics. A
  material shortfall against it requires primary-score work or a proven
  enabler; runtime, kernels, serialization, calibration, documentation and
  status copying are secondary.
- Public task-specific papers, discussions and source are allowed when operator
  policy permits; only imported answers, labels or predictions are forbidden,
  and Skills cannot narrow that policy. These requirements override
  incompatible vertical style instructions such as compulsory kernel invention,
  profiling, or bans on task-specific sources.
- Before proposing work, index the recorded experiment outcomes and rule out
  repeated ideas, including renamed variants.
- Validation, OOF, calibration and blend selection must use models fitted
  without the scored rows' labels; a final all-train refit is test-only
  evidence.
- Every task needs `impact_score` 1..5, `impact_area` and `evidence`; reserve
  4-5 for direct target movement or a proven prerequisite.
- Controller feedback files are live truth.
