---
name: "Experiment Evidence Audit"
description: "Read-only inspection of the code, configuration, evaluator, and raw evidence behind a research claim."
---

# Experiment Evidence Audit

Use this inside Experiment or as part of the final scientific Review. Inspect
the direct code path, explicit run configuration, evaluator, positive control,
baseline execution, and raw result rows. Do not create an audit file.

Block interpretation when:

- the executed method differs from the claimed method;
- gold or scorer information leaks into predictions;
- the positive control fails;
- a published baseline is replaced by a renamed local heuristic;
- the evaluator cannot discriminate the target behavior;
- result rows are missing, duplicated, selectively dropped, or inconsistent
  with the paper;
- the comparison changes information, compute, data, or scoring unfairly.

Return the concrete defect and smallest repair through the normal Reviewer
response. Implementation and evaluator failures stay in the current stage and
never reopen Idea selection.
