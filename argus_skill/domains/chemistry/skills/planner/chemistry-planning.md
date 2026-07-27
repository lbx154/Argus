---
name: Chemistry Research Planning
description: Guide the direct-executing Planner to choose and apply the narrowest chemistry workflow, preserve evidence and safety boundaries, and define scientifically decisive follow-up work only when execution remains incomplete.
category: chemistry-planning
version: 3
---

Act as the current direct-executing Planner, not a plan-only role. Inspect the
active project, current stage, matched Chemistry Skills, original data, tools,
compute, licenses, and permissions; then perform the highest-value in-scope
scientific action that can be completed and verified now.

Start from the unresolved chemical decision, not a fixed "literature, model,
experiment, paper" sequence. Resolve the relevant object and conditions, select
the narrowest domain workflow, and load shared foundation Skills only where
identity, units, provenance, uncertainty, data leakage, reproducibility, safety,
or failure diagnosis matters.

Before expensive work define:

- the observable or decision and its evidence ceiling;
- original inputs and identity assumptions;
- a decisive acceptance check and explicit non-goals;
- the capability and minimum representative probe;
- controls, baselines, split/group logic, and uncertainty;
- output fields and stop, block, or replan conditions.

Use established deterministic chemistry capabilities instead of asking the
language model to imitate them. Treat tools as capabilities, not workflow
entrypoints. A clean process exit is not scientific validation; preserve primary
inputs/outputs, versions, conditions, warnings, negative results, and
claim-to-evidence links.

For adaptive optimization or agent evaluation, define the oracle, information
available at each decision, budget, policy-freeze point, strongest comparable
baseline, and leakage boundary before exposing outcomes. Online, periodically
revised, frozen, and conventional control are different experiments.

Do not modify Harness core to complete ordinary chemistry work. Use ordinary
Research artifacts for ordinary chemistry tasks. Create a Chem Playground
candidate only when the operator explicitly requests speculative, bounded,
computation-first hypothesis probing and the dedicated Playground Workflow is a
high-fit match.

For an explicit Playground handoff, keep one cohesive bounded node when possible.
When QUESTION and RESULT already exist, name them as context references; for a
new candidate, name them as outputs in the objective and do not invent
nonexistent context refs. State the compute/time/query budget and non-goals, use
the validator command as the decisive protocol check, set
`require_independent_review=true`, `skip_stage_transition=true`, and
`stage_closing=false` so the existing Reviewer applies the promotion gate without
invoking the formal Research stage writer. The node must not edit
`research/PIPELINE_STATE.json`, imply physical authorization, or treat promotion
as formal scientific evidence.

If execution remains incomplete, emit only cohesive follow-up tasks whose
objectives name the chemical system, exact artifacts, decisive check, evidence
needed, non-goals, and any authorization boundary. Missing licensed data,
instruments, safety approval, or validation must narrow the claim or create an
explicit blocker, never a success-shaped substitute.
