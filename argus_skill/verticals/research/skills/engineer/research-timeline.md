---
name: "Research proposal timeline"
description: "Estimate paper proposals, arrange experiments around dependencies and resources, and explain schedule revisions."
---

# Research proposal timeline

Use when the operator requests a proposal, a concrete completion estimate, an
experiment schedule, or a revised deadline. This is a planning aid, not a new
research completion gate. Keep existing stage ownership and selection policy.

1. Draft each candidate proposal from the operator's idea and sources: thesis,
   implementation difficulty, decisive future experiment, main comparisons,
   claim-critical controls/ablations, analysis, writing, and review. Name the
   actual model, dataset, baseline, seeds, and hardware in task titles/basis
   where known. State unknowns and assumptions instead of inventing facts.
   Use existing portfolio route IDs or the operator's locked idea ID. During
   Idea, estimates stay source-only; do not run probes to price a candidate.
2. Supply lower, most likely, and upper elapsed hours for each task, with a
   short basis: measured comparable runtime, published runtime, or explicitly
   uncalibrated judgment. Include model/code difficulty and queue/setup risks
   in the basis. Do not invent a universal difficulty multiplier. Hours count
   from a common project start, with continuous availability; include offline
   human turnaround in durations. Reserve named resource slots for both GPU
   work and limited researcher/agent attention. Split jobs when resources are
   only occupied during part of a stage.
3. Write the proposal input under `.argus/timeline/proposal.json`. Use this
   minimal shape (extend tasks and proposals to cover the real work):

   ```json
   {"selected_proposal_id":"route-01","resources":{"gpu":1},
    "deadline_hours":168,"now_hours":0,"defer_optional":false,
    "proposals":[{"id":"route-01","title":"Selected research idea",
      "assumptions":["Source-only estimate; validation may falsify the premise"],
      "tasks":[{"id":"pilot","title":"Decisive validation comparison",
        "phase":"validation","duration_hours":[4,12,48],
        "depends_on":[],"resources":{"gpu":1},"optional":false,
        "basis":"Uncalibrated estimate based on comparable workload"}]}]}
   ```

4. Preview with
   `python -m argus_skill.verticals.research.timeline --input .argus/timeline/proposal.json`.
   Add `--json` for structured output. Present the point estimate, scenario
   range, remaining time, experiment ordering, resource queues, and deadline
   gap. Proposals are alternatives; the tool does not select an idea or launch
   experiments. A deadline is a target, not permission to weaken the claim.
   Set `defer_optional=true` only for work the operator/Planner already treats
   as optional; the tool protects required dependency closure.
5. Record the original forecast by adding `--project-root . --expected-version 0
   --reason 'Initial proposal'`. Revisions use the most recent version.
   `.argus/timeline/000001.json` and later numbered files are immutable
   forecast snapshots, not a replacement for runtime events or verdicts.
6. After a meaningful experiment result, resource change, missed milestone,
   or user idea/deadline selection, update `now_hours` and actual progress.
   Running tasks require `actual_start_hours` and a fresh three-point
   `remaining_hours`. Completed/failed tasks require actual start/finish;
   report `failed` only from real evidence, not a timeout. Failed/blocked tasks
   require `reason`. Supply task-specific reasons and evidence references for
   delays (e.g. evaluator repair, lower throughput, GPU queue, provider outage,
   additional control needed, premise refuted). If unknown, say so and name the
   next diagnostic. The tool surfaces unknown causes without inventing one.
7. Planner decides the response to failed validation. Preserve the failed
   task and its evidence; if explicitly retired from the delivery path mark it
   optional and add replacement work with new IDs and corrected dependencies.
   Never reset successful or failed runs to pending. Keep the same project
   clock, record the revision reason, and report original versus current ETA.
   An unresolved required failure blocks a defensible completion forecast;
   show the previous estimate with that limitation and propose the missing
   recovery work. Unknown future pivots can exceed the original upper scenario.

The CLI/API calculate forecasts. Engineer records observed state and Planner
decides executable backlog revisions through the existing runtime. This tool
does not monitor jobs, change deadlines in GoalContract, or dispatch work.
