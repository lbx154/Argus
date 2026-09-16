---
name: "Keeping the hypothesis and implementation aligned"
description: "Keep the selected thesis and the code that tests it aligned during Experiment."
---

# Keeping the hypothesis and implementation aligned

Use this in Experiment after Idea selection and before claim-bearing execution. Read
the selected thesis from the research notes in `RESEARCH_NOTES.md` and the
method as stated in the project-root `METHOD.md` (`engineer/method-card.md`);
the card's `Components` table is where the mapping below is recorded, one
row per component with `path:Symbol` and the `tests/spec` test that proves it.

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

Fix `MISMATCH` or `NOT_IMPLEMENTED` in place before claim-bearing runs, and
update the affected `METHOD.md` component rows in the same round when the
method itself changed. Do not reopen Idea selection. The alignment result
lives in the host-derived component status (from the `tests/spec` markers and
the host's run) and in the Reviewer's returned judgment, not in a separate
note; the research notes in `RESEARCH_NOTES.md`, written at Paper
entry, may point to the card.
