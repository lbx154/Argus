---
name: "Research Experiment Playbook"
description: "The single authoritative playbook for adaptive experiments that earn a strong positive paper result."
---

# Research Experiment Playbook

## Outcome

Develop the selected method until representative evidence supports one strong
positive thesis against the strongest fair alternatives. The target is a result
that a top reviewer would remember, not a complete-looking experiment matrix.

## Work

1. Start from `HANDOFF.md` and the runnable Build outputs.
2. Use real models or systems, public or official benchmarks, authentic
   evaluators, and the strongest same-information published baselines required
   by the claim.
3. Keep every run reproducible from its code, explicit configuration, command,
   and raw output.
4. Treat weak results as optimization signals. Change the method, implementation,
   benchmark, baseline, controls, or scale when development evidence identifies
   a concrete reason.
5. Separate small engineering diagnostics from claim-bearing experiments.
   Stop repeating micro-benchmarks once they no longer change the next decision.
6. Use held-out confirmation after method and evaluation choices stabilize.
7. Measure uncertainty and resource cost at the level the paper claim requires.

Choose benchmarks that expose the method's mechanism and real advantage rather
than convenient saturated tasks. Follow surprising positive evidence when it
reveals a stronger contribution, then confirm it on untouched data. Keep
relevant losses visible internally, but do not let defensive edge-case coverage
replace the main result.

Do not freeze a global experiment plan, reopen Idea selection, hide relevant
losses, or convert an unfinished campaign into a negative-result paper.

## Paper entry

Remain in Experiment unless mechanism-relevant wins clearly exceed losses, the
headline and primary comparisons win, and the strongest same-information
baseline is beaten. Manager alone advances the stage.

## Handoff

When the entry bar is met, replace project-root `HANDOFF.md` with
`# HANDOFF — EXPERIMENT`. Include the thesis, winning comparisons, strongest
baseline, relevant limitations, confirmed figures/data, and minimum
reproducibility pointers needed by Paper. Organize the handoff around the claim
and its evidence, not around the order in which experiments ran.

## Optional tools

Use setup diagnosis, research-grind, ablation, evidence-audit, and
results-analysis Skills only for the current experiment question. They do not
define stage completion or create parallel plans.
