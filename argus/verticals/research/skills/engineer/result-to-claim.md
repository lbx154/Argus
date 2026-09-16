---
name: "Finding the claim in the results"
description: "Use direct experimental evidence to decide how to improve the method next or what the paper can claim."
---

# Finding the claim in the results

Use this in Experiment. Read direct raw results, executed configuration, code,
positive controls, evaluator outputs, and strong baseline results.

Use the evidence to decide:

1. what the evidence currently supports;
2. whether implementation, evaluator, benchmark, scale, or method explains the
   largest gap;
3. the next highest-information method or experiment change;
4. whether relevant wins clearly exceed losses and the headline comparisons
   beat the strongest same-information baseline.

Write what the evidence supports into `experiments/claims.json` as you go:
one entry per claim-bearing comparison with metric, direction, ours and the
strongest measured baseline (mean, spread, repeats), the other baselines, the
raw evidence files, and an honest status (`supported`, `inconclusive`,
`refuted`). Fill the arms from the raw rows. A comparison within run-to-run
spread is inconclusive; a headline needs a real benchmark. Run
`python -m argus.verticals.research.experiment_claims validate` to see what
the stage still lacks.

If the paper-entry bar is not met, keep improving the method in Experiment.
Do not reopen Idea selection or turn failed attempts into a negative-result
paper; a negative or boundary thesis reaches Paper only when its evidence is as
complete as a positive one would need. When the bar is met, rewrite the research
notes in `RESEARCH_NOTES.md` with the positive thesis,
decisive comparisons, essential contrary evidence, and direct sources Paper
needs.
