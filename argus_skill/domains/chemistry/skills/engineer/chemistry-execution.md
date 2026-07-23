---
name: Chemistry Research Execution
description: Add chemistry-specific execution guidance to research missions using real chemistry tools, primary outputs, comparable baselines, and honest evidence boundaries.
category: chemistry-execution
version: 1
---

Do the chemical work in the form that fits the question. Distinguish observed,
computed, simulated, predicted, retrieved, and inferred results; do not describe
one as another.

Inspect the real environment before selecting a tool. Install heavy or
domain-specific dependencies only in the project's environment or container, not
in the Argus harness. Run a small capability probe before committing a long
campaign. If tool selection is uncertain, consult the matchable
`engineer/chemistry-toolkit.md` skill and then verify current official
documentation, versions, data licenses, model weights, and API permissions.

Preserve the inputs and primary outputs needed to reproduce the claim. Check
identifiers, molecular or material structures, stereochemistry, charge and spin,
units, conditions, approximations, random seeds, software versions, and
convergence settings where they matter. Keep failed calls and negative results;
do not replace unavailable real evidence with toy data while retaining the
original claim.

For a sequential campaign, let returned observations change later proposals and
stay within the declared query or experiment budget. Run the strongest
appropriate baseline under the same budget. Keep evaluation answers hidden from
proposal logic and use time, scaffold, system, or task splits when the benchmark
requires them.

Record where intelligence enters the policy before implementation. Distinguish an online agent choosing
each action, an agent-designed policy frozen before outcomes, and an ordinary
optimizer executed by the harness. Do not label an agent-designed fixed policy as
online agent control or use its result to claim online agent sample efficiency.
If the requested experiment tests online Argus decisions, route each budgeted
decision through the live agent and retain its observation context and action.
Do not compile a heuristic in its place; reduce the evaluation budget or report
that the requested online experiment is infeasible.

State the evaluator threat model before calling answers hidden or sealed. A
subprocess running as the same user is useful interface separation, but it is not
adversarial sealing when the agent can still read or edit evaluator files. For an
anti-cheat claim, use an external or OS-enforced capability boundary the evaluated
agent cannot bypass; otherwise limit the claim to cooperative protocol compliance.

Physical actuation is permitted only through an already authorized capability
whose facility or instrument layer enforces safety limits and interlocks. Never
work around that layer. Without such access, restrict the result to computation,
analysis, planning, or an explicit blocker.

Use project-native chemistry files and the existing `CHECKPOINT.md`. Do not
create process-only manifests, ledgers, audit packets, or status files merely to
look rigorous; the actual source data, code, tool output, and scientific result
are the evidence.
