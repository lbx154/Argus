---
name: "Auto Research Pipeline"
description: "Build a selected research idea through the forward-only idea, build, experiment, paper, and review workflow."
---

# Auto Research Pipeline

The canonical research stages are:

```text
idea -> build -> experiment -> paper -> review
```

Stages never roll back. If later work exposes a method, evaluator, experiment,
or manuscript defect, keep the current stage and repair it there. Stage state
is Manager-owned; Engineer and Reviewer report evidence and next actions.

## One handoff

Idea, Build, Experiment, and Paper use project-root `HANDOFF.md` as their only
normal cross-stage handoff. At each transition, replace the file completely
with the minimum upstream context the next stage needs and start it with the
current stage marker: `# HANDOFF — IDEA`, `# HANDOFF — BUILD`,
`# HANDOFF — EXPERIMENT`, or `# HANDOFF — PAPER`. Do not append history, impose
another schema, or create parallel handoff files.

Review does not load `HANDOFF.md`. It uses `paper/main.tex`, its rendered output
and `paper/REVIEW.md`, then follows direct claim-critical references to executed
code, explicit configuration, raw rows, real evaluators, and primary sources.
It does not recursively crawl history.

## Build

Implement the selected mechanism and strongest fair baseline through the real
entry points. Reproduce real strong published baselines rather than renamed local
heuristics; use current models, appropriate public or official benchmarks, and
real evaluators where the claim requires them. Keep explicit run configuration
with the code. Trace the hypothesis to the quantities the executed path actually
computes, and run a known detectable positive control before interpreting null
or negative evidence. Code and direct test output are the evidence; do not
create extra bookkeeping artifacts.

## Experiment

Keep each run reproducible through its code, explicit configuration, command,
and raw outputs. The programme remains adaptive: development evidence may
change the method, baselines, benchmark design, controls, and next experiments.
Do not freeze a global plan.

Enter Paper only when mechanism-relevant wins clearly exceed losses, headline
and primary comparisons win, and the strongest same-information baseline is
beaten. Otherwise continue method work in Experiment.

## Paper

Write a thesis-driven, persuasive paper around the contribution and strongest
supported result. Do not produce a negative-result report or experiment
chronology. Paper produces a complete compilable draft with every intended
experiment, figure, table, citation, and venue-required section. Final visual
inspection, academic-language polishing, and whole-paper acceptance happen only
in Review.

## Review

Run scientific, strict visual, and academic-language passes in parallel on the
same paper, repair their combined findings, recompile, and obtain one integrated
independent decision. An authoritative review overwrites `paper/REVIEW.md`.
Repair reject-level issues inside Review. Do not create another review file or
request to move backward. Review is the terminal certified stage.
