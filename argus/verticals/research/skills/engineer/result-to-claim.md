---
name: "Reading the results against the fixed claim"
description: "Use direct experimental evidence to decide which rung of the diagnosis ladder to work next, and, once the claim as stated is supported, what the notes hand to Paper."
---

# Reading the results against the fixed claim

Use this in Experiment. Read direct raw results, executed configuration, code,
positive controls, evaluator outputs, and strong baseline results.

The claim is the one in `METHOD.md`, fixed at Idea selection; the results do
not choose the claim, they choose the next change to the implementation. Use
the evidence to decide:

1. what the evidence currently supports, cell by cell, against the claim as
   stated;
2. which rung of the diagnosis ladder in `research-experiment-playbook.md`
   explains the largest gap: implementation fidelity, setup and evaluator,
   hyperparameters and recipe, scale and data, baseline fairness, or a
   method variant that still satisfies the claim;
3. the one diagnosed change with the highest information value, and the
   evidence that will show whether it worked;
4. whether the headline comparisons now beat the strongest same-information
   baseline with relevant wins clearly exceeding losses.

If the claim as stated is not yet supported, keep working the ladder in
Experiment and record each attempt's rung and evidence. Do not reopen Idea
selection, restate the claim to match what the code did, or turn failed
attempts into a negative-result paper. After three distinct, diagnosed
attempts, escalate to the operator with the evidence; only the operator
changes the claim. When the claim is supported, rewrite the research notes
in `RESEARCH_NOTES.md` with the claim as stated, the decisive comparisons,
essential contrary evidence, and direct sources Paper needs.
