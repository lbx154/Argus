---
name: "Reading the evidence behind a claim"
description: "Read the code, configuration, evaluator, and raw evidence behind a research claim without changing them."
---

# Reading the evidence behind a claim

Use this inside Experiment or as part of the final scientific Review. Inspect
the direct code path, explicit run configuration, evaluator, positive control,
baseline execution, and raw result rows. Do not create a separate file to
record the inspection.

The evidence cannot yet support interpretation when:

- the executed method differs from the claimed method;
- gold or scorer information leaks into predictions;
- the positive control fails;
- a published baseline is replaced by a renamed local heuristic;
- the evaluator cannot discriminate the target behavior;
- result rows are missing, duplicated, selectively dropped, or inconsistent
  with the paper;
- the comparison changes information, compute, data, or scoring unfairly;
- the labels are stipulated where the claim needs derived or human-validated
  ones, or a constant answer already matches the headline.

## Before trusting a benchmark

A benchmark carries a claim only if its labels, balance, and scoring can. Ask,
with the raw items and the code in front of you:

- Where does each gold label come from: derived by a solver or a theory whose
  rule is written down, imported from published human judgments, or typed in
  by the project? Constructed tasks can derive gold from explicit executable
  rules; accept all valid answers. Author expectations alone cannot ground a
  claim that a model fails the task.
- Does a constant answer already score well? Accuracy against the majority
  label, per construction and per cell, tells whether the benchmark measures
  anything.
- Can the target variables be told apart on the items? When two targets share
  the same gold on nearly every item, no accuracy can show that a model
  distinguishes them.
- Do the contexts or prompts state the answer in other words, so that a
  shortcut reads it off? Would a same-information classifier on the surface
  text do as well?
- What is the score? A two-way softmax over yes/no verbalizers is a restricted
  score, not a calibrated probability; single tokens, casing, and leading spaces
  change it; forced choice can hide that free generations are unparseable.
  Look at what free generation actually produced.
- How many independent units remain once templates, worlds, and repeated
  contexts are collapsed? Uncertainty belongs to that unit, not to the row.
- Are model families compared under the same rendered prompt, template,
  decoding, and precision, or does the comparison change those as well?

Return the concrete defect and smallest repair through the normal Reviewer
response. Implementation and evaluator failures stay in the current stage and
never reopen Idea selection.
