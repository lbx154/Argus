---
name: "Metric Optimization Project Contract"
description: "为明确要求优化某个实测指标的项目建立简洁约定。 Write or revise an optimization task contract when the requested deliverable is an improved measured metric; preserve correctness, scope, resource limits and the real stopping condition."
---

# Metric Optimization Project Contract

Use when the operator explicitly wants a measured optimization, across software,
hardware or another quantitative workflow. It does not choose a project's vertical,
replace existing repository guidance or remove a requested paper/report deliverable.

Before editing `AGENTS.md`, read the existing instructions. Add only the missing
project-specific contract, using current operator authority. Fill the actual values;
never leave a generic template that silently overrides known requirements.

## Minimal contract to record

- **Objective:** the metric, direction, workload and accepted measurement command.
- **Correctness:** the unchanged validity/evaluation conditions that every candidate
  must meet. A faster incorrect result is not a valid improvement.
- **Baseline:** the identified implementation, revision, inputs, allocation and
  protocol used for comparison. Preserve fixed references selected by the user.
- **Resources:** current hardware/API access, budget, time constraints and cancellation.
- **Scope:** allowed edits, protected evaluator/inputs and explicitly excluded work.
- **Stop condition:** the actual target or authorized search/time budget. Exhausting
  the budget is a truthful stopping reason, not proof the target was reached.

## Run the smallest informative optimization cycle

1. Understand the workload and measurement path. Measure a valid baseline under the
   agreed conditions before drawing conclusions about a bottleneck.
2. Use a profiler or decisive experiment appropriate to that workload. Python,
   native code, GPU kernels and services require different measurement tools.
3. Make one coherent change linked to the observed limiting factor. Check correctness,
   then compare repeated measurements when noise matters.
4. Keep useful changes; revert regressions or unsupported complexity. Record meaningful
   changes and actual numbers in the existing experiment/benchmark record.
5. Report the measured outcome, comparison conditions and unresolved limitations.

Do not fabricate scores, weaken a verifier, hide failed trials, or equate an
estimated speedup with a measured one. An unavailable resource requires a concrete
boundary or an authorized alternative, not a mock benchmark result. Follow the
shared environment/cache guidance rather than embedding host-specific paths here.
Keep planning and reporting proportional to the task; no extra academic pipeline
or parallel Team is implied by an optimization objective.
