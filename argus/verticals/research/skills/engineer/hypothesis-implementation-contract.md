---
name: "Keeping the hypothesis and implementation aligned"
description: "Keep the selected thesis and the code that tests it aligned during Experiment."
---

# Keeping the hypothesis and implementation aligned

Use this in Experiment after Idea selection and before claim-bearing execution. Read
the selected thesis from the research notes in `RESEARCH_NOTES.md`; do not
create a separate file stating the terms of the comparison.

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

Fix `MISMATCH` or `NOT_IMPLEMENTED` in place before claim-bearing runs. Do not
reopen Idea selection, and do not write a separate note for the alignment
result — the research notes in `RESEARCH_NOTES.md`, written at Paper entry,
are the only such account.
