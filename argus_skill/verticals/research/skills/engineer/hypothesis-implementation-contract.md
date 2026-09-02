---
name: "Hypothesis-Implementation Alignment"
description: "Keep the selected thesis and the code that tests it aligned during Build."
---

# Hypothesis-Implementation Alignment

Use this in Build after Idea selection and before claim-bearing execution. Read
the selected thesis from `HANDOFF.md`; do not create a separate contract file.

Map every load-bearing part of the thesis to the actual implementation:

- mechanism, intervention, prediction, and falsifier;
- executable entry point and branch where candidate and baseline diverge;
- formulas, operands, masks, reductions, timing, and gradient boundaries;
- information available at decision time;
- evaluator output and positive control;
- fair baseline and invariants.

Implement through those concrete paths. Then have a fresh Reviewer inspect the
selected thesis and the reachable call chain and return exactly one conclusion:

- `ALIGNED`: the code tests the selected mechanism under the intended comparison;
- `MISMATCH`: the code runs but tests a different mechanism or comparison;
- `NOT_IMPLEMENTED`: the selected mechanism is absent or unreachable.

Fix `MISMATCH` or `NOT_IMPLEMENTED` in Build. Do not reopen Idea selection.
When Build is complete, replace `HANDOFF.md` with `# HANDOFF — BUILD` and include
only the implemented mechanism, real entry point, run configuration, baseline,
evaluator, alignment result, and remaining experiment risks.
