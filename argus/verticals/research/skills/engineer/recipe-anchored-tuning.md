---
name: "Recipe-anchored Tuning"
description: "以所选框架在钉住 SHA 处的官方示例配置为锚,一次只改一个因子地做小规模试点。 Anchor on the chosen framework's current example config at the pinned SHA, derive task-bound knobs from a dev pool, and tune one factor per supervised pilot before any full run."
---

# Recipe-anchored Tuning

Use after `engineer/framework-stand-up-pilot.md` chose a framework. The
official example at the pinned SHA is the baseline configuration; the method is
a readable diff against it, never a config written from memory.

## Anchor

Copy the framework's CURRENT example config nearest the task from
`third_party/<name>/` at the pinned SHA into the project, with a provenance
comment at the top (source path, SHA, copy date). Every later change is a diff
against this file.

## Task-bound knobs from a dev pool

Derive from a dev pool disjoint from the held-out evaluation set:

- trajectory allowance from a high percentile of correct-solution lengths,
  counting tool observations as tokens
- tool-iteration cap from the distribution of iterations in correct solutions
- reward reachability: the reward/extraction path returns the intended value on
  known correct and incorrect examples before any training step

## One factor per pilot

Fix the pilot harness (same prompts, seeds, device, step count) and change one
factor per pilot, in this order:

1. learnability knobs first: group size, allowance, reward normalization
2. LR, KL coefficient, clip range only when grad norm, KL or entropy traces ask
   for it
3. throughput knobs (batching, engine memory fraction, parallelism) last; they
   must not change the objective, and a pilot must show the loss terms unchanged

Run pilots under the durable runner's supervised mode with
`engineer/rl-training-collapse-diagnosis.md` as the health reference. Record one
row per pilot in the project decision record: factor changed, value, contrast
fraction, grad norm and KL ranges, s/step, peak memory, verdict.

## Stop rule

Stop tuning when two consecutive pilots on the same factor move the learnability
signals by less than their pilot-to-pilot noise, or when the budget reserved for
tuning is spent. Record the stop reason.

## Escalation note before any full run

Write an escalation note in `RESEARCH_NOTES.md` before the first full run, with:
reward contrast in N of M groups over K pilot steps; s/step times planned steps
within budget with a stated margin; peak memory at the maximum allowance;
checkpoint save and resume tested on the pilot; evaluation cadence; and the
statement that held-out data was untouched by every pilot. The Planner and
Reviewer judge this note; it is evidence, not a gate.
