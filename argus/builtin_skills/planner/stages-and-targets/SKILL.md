---
name: "Stages, direct work and targets"
description: "How the Planner treats the workflow stage, direct work, the final certification task, a research target and open-ended work."
---

# Stages, direct work and targets

## Staged work

Use the stage as context, not a boundary. Choose the operator's most valuable
next milestone; the Manager updates stages from mission results.

## Direct work

The stage names the kind of work, without requiring a separate set of files;
treat it as context, not a hard boundary. Delegate the implementation,
experiment or verification that directly advances the operator objective, and
nothing beside it: the bound is on what you build, never on how much you
measure. Do not create, repair or confirm sets of stage files, frontier
snapshots, stage state, checkpoints, reports or setup documents unless the
operator asked for that output or the work strictly needs it. Existing records
are optional evidence; their absence must not displace substantive work.

## The final certification task

At the final stage of a vertical with a certified final stage or a research
target, the final independent certification task sets
`scope: "final_submission"` so the Reviewer's acceptance can close the stage.
Ordinary prerequisite work keeps the default scope.

## Research target

Preserve `research_target_level` from `.argus/PIPELINE_STATE.json`: it sets
`to=done`, not this round's bar. At `publishable` or `doctoral`, require
technical depth, verified originality, formal or causal grounding and field
significance. A literature review instead needs independent checks of scope,
coverage, synthesis, claims and writing at that level; originality is
unnecessary. Known results, finite checks and honest negatives are progress,
not done, except that independently verified negatives may suffice at
`exploratory`.

## Verification staging

Plan only the evidence this stage's verification profile needs; the vertical's
final stage owns certification. Integrity and truth do not get weaker in
earlier profiles.

## Open-ended work

Continue until the operator stops it. Normally report and close completed
rounds. Justify a new round's value in one sentence, and address behavior
reachable through a real entry point.
