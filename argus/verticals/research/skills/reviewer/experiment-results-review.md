---
name: "What the experiments establish"
description: "Read the current experimental evidence and decide how the method should develop next."
---

# What the experiments establish

Use this in Experiment. Read the review packet in your context first (the
component anchors with their code excerpts, the host-run test outcomes, the
config changes and the files changed this round), then `METHOD.md`, then
direct code, configuration, raw results, positive controls, evaluator outputs,
and strong baseline results. Do not write a separate result-review file.

The claim is the one in `METHOD.md`, fixed at Idea selection. You judge whether
the implementation and the evidence satisfy that claim; you never accept claim
drift. A result presented against a narrower claim than the card states, a
"restricted case", or a negative result with fewer than three distinct,
diagnosed attempts on the playbook's diagnosis ladder is a repair request:
return `continue`, name the rung the Engineer should work next and the
evidence that would settle it. Only the operator changes the claim; when the
evidence after three diagnosed attempts still contradicts it, say so plainly
so the Planner can raise the operator question with the evidence.

## Questions that determine the next experiment

- Is the executed path faithful to the selected mechanism? Does every
  component in the card carry a `# @component` anchor on its entry point and a
  knockout that fails in its absence? A component without either is a repair.
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
  same-information baseline, which rung of the diagnosis ladder has not yet
  been worked with evidence: implementation fidelity, setup and evaluator,
  hyperparameters and recipe, scale and data, baseline fairness, a faithful
  method variant? That rung is the next repair, not a restated thesis.
- Which concrete method or experiment change has the highest information value?

When a result is weak, first diagnose the implementation, evaluator, benchmark,
scale, or method. Keep the selected Idea, the claim as stated, and the current
stage. The experiment programme may change from development evidence; the
claim may not.

Recommend Paper when credible evidence, at the scale the claim needs, supports
the claim as the card states it and improves at least one scientifically
meaningful dimension. Say plainly when development has cycled on the same panels:
repeated development is not confirmation. Do not require a
hard numeric margin, wins on every headline metric, or dominance over every
strong baseline. Keep uncertainty, relevant losses, and tradeoffs visible;
otherwise return one concrete Experiment repair through the normal Reviewer
response.
