---
name: "Research Experiment Playbook"
description: "The single authoritative playbook for adaptive experiments that earn a strong positive paper result."
---

# Research Experiment Playbook

## Outcome

Develop the selected method until representative evidence supports a scoped
thesis with scientific value. A credible improvement in any meaningful
dimension can carry the contribution; the target is a result that a top
reviewer would remember, not a complete-looking experiment matrix.

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

Enter Paper when Reviewer judges that credible evidence improves at least one
scientifically meaningful dimension. Do not require a hard numeric margin,
wins on every headline metric, or dominance over every strong baseline. Keep
uncertainty, relevant losses, and tradeoffs visible, and scope the thesis to
what improved. Manager alone advances the stage.

## Handoff

When the entry bar is met, replace project-root `HANDOFF.md` with
`# HANDOFF — EXPERIMENT`. Include the thesis, winning comparisons, strongest
baseline, relevant limitations, confirmed figures/data, and minimum
reproducibility pointers needed by Paper. Organize the handoff around the claim
and its evidence, not around the order in which experiments ran.

## Progressive disclosure

Start with this Playbook. Open one specialist Skill only for the current
decision, then return here. Do not preload the table.

| When needed | Open | Use it for |
|---|---|---|
| The method is below its baseline | `engineer/research-grind.md` | Diagnose and improve the largest live gap |
| The run may be misconfigured | `engineer/suspect-the-setup.md` | Separate setup failure from method evidence |
| A mechanism needs one decisive ablation | `engineer/ablation-planner.md` | Choose only claim-changing ablations |
| Raw evidence or evaluator behavior is disputed | `reviewer/experiment-audit.md` | Inspect code, configuration, evaluator, and rows |
| The next experiment or Paper decision is unclear | `reviewer/experiment-results-review.md` | Independently judge the evidence frontier |
| Results must become a precise claim | `engineer/result-to-claim.md` | Bind direct evidence to the strongest supported thesis |
| Confirmed results need tables or figures | `engineer/research-results-analysis-and-figures.md` | Produce claim-bearing paper visuals |

Specialist Skills answer one experiment question. They do not define a global
plan, stage transition, or parallel report.
