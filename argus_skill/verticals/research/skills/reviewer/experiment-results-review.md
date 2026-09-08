---
name: "What the experiments establish"
description: "Read the current experimental evidence and decide how the method should develop next."
---

# What the experiments establish

Use this in Experiment. Read direct code, configuration, raw results, positive
controls, evaluator outputs, and strong baseline results. Do not write a
separate result-review file.

## Questions that determine the next experiment

- Is the executed path faithful to the selected mechanism?
- Did the evaluator and positive control work?
- Do the raw observations actually cover the declared configurations and
  repetitions, with exclusions or failures visible and claim-critical
  invariants satisfied? Recompute the relevant aggregate from those rows;
  a success flag, old table, or copied completion record is not sufficient.
- Does the experiment directly test the claimed capability? Which control
  distinguishes it from the strongest shortcut explanation?
- Were baselines real, competitive, and fairly resourced?
- Does the benchmark exercise the claimed mechanism, and does it hold up on
  its own: label provenance, a constant-answer baseline, targets that the
  items can separate, no shortcut in the context, and a score whose meaning
  is known? Open `reviewer/reading-the-evidence.md` when any of these is unclear.
- Is the observed difference larger than relevant uncertainty?
- Is the evidence at the scale the claim needs: families and sizes for a claim
  about models, independent items or tasks for a claim about a phenomenon, the
  matched ablation for a claim about a mechanism? The compute and cached
  weights shown in the stage context are what was available.
- If the mechanism lost to its matched ablation or the strongest
  same-information baseline on fresh evidence, is the highest-value next action
  re-deriving the thesis from what the evidence establishes, rather than
  another variant of the same objective?
- Which concrete method or experiment change has the highest information value?

When a result is weak, first diagnose the implementation, evaluator, benchmark,
scale, or method. Keep the selected Idea and current stage. The experiment
programme may change from development evidence.

Recommend Paper when credible evidence, at the scale the claim needs, improves
at least one scientifically meaningful dimension and the scoped result has
research value. Say plainly when development has cycled on the same panels:
repeated development is not confirmation. Do not require a
hard numeric margin, wins on every headline metric, or dominance over every
strong baseline. Keep uncertainty, relevant losses, and tradeoffs visible;
otherwise return one concrete Experiment repair through the normal Reviewer
response.
