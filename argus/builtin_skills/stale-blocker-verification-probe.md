---
name: "Verify a Recorded Blocker or a Changing External Fact"
description: "核实记录中的阻塞或决策所依赖的外部事实是否仍然成立。 Recheck a recorded blocker, or a mutable external fact the current decision depends on (model access, library versions, API endpoints, benchmark protocols, prices), with the cheapest decisive authorized probe; distinguish still blocked, cleared and insufficient evidence, and separate identity, permission and execution before relying on a resource."
---

# Verify a Recorded Blocker or a Changing External Fact

Two situations call for the same discipline. A prior record says a specific
action is blocked, and the record may be stale. Or a fact outside the
repository — which checkpoint is downloadable, which library version is
published, what an API endpoint returns, what a benchmark's protocol requires —
could change the implementation or the claim, and your memory of it may be
stale. In both cases the record is a hypothesis, the question is narrow, and the
answer comes from the cheapest observation that can actually decide it.

Do not start every task with a survey. Local edits, mathematics and stable
language semantics are decided by the existing repository and its tests; a web
search there costs time and invites drift. Verify an external fact when a
decision depends on it, and stop when the evidence is sufficient for that
decision. Do not re-investigate the whole project, repeat a costly failed run,
or write a separate research report.

## Ask a question a probe can answer

1. Translate the blocker or the fact into the condition that matters: resource
   access, whether a file is valid, a missing decision, a dependency version, an
   endpoint's behaviour, a benchmark protocol, or a specific verification result.
2. Look at what you already have. The configured environment, the lockfile and
   recent task evidence often settle the question; reuse that evidence while its
   version and conditions still apply.
3. Choose the cheapest authorized probe that can decide what remains: a file
   check, a structured field, a small access request, one targeted test, or a
   query to the authoritative source. `pip index versions <package>` shows what
   is published and the installed interpreter shows what actually runs; neither
   proves the two are compatible. Before citing the behaviour of documentation
   or released code, fetch it; a search result locates a source, it does not
   prove what the source says.
4. Record the probe's scope, its actual outcome and what it did not cover.

## Identity, permission and execution are three questions

A resource that exists is not yet a resource you may use, and one you may use
is not yet one that runs. Separate identity, permission and execution whenever
a model or checkpoint is involved:

- `https://huggingface.co/api/models/<id>` returning 200 shows that the
  metadata is visible. Read `gated`, the license and the revision; visibility
  does not grant downloads.
- Probe one small required file at the pinned revision with the credentials
  already authorized for this task. A 401 or 403 marks an access boundary, and
  an unauthenticated rejection says nothing about the configured account.
- Download permission does not prove framework support or hardware capacity.
  Run a minimal load or inference only when execution readiness is what the
  decision needs.

Never print credentials or signed download URLs. The same separation applies
elsewhere: a file that exists may be invalid, visible metadata is not a grant,
and missing telemetry does not mean the run never happened.

## Read the result honestly

- `CLEARED`: the actual prerequisite is now established.
- `STILL_BLOCKED`: direct evidence shows the prerequisite is not satisfied.
- `INSUFFICIENT_EVIDENCE`: the probe could not distinguish the possibilities.

An inconclusive probe is not a coin to flip. Choose the next inexpensive
discriminating observation within budget, or say what evidence is missing and
report the fact as unknown.

## Then act within the boundary

When cleared, resume the smallest useful permitted action. When still blocked,
preserve the evidence and continue independent work; route only a concrete
unavailable decision or resource through the current role. A status check is
not authority to edit completion requirements or override project state.

When a dependency is unavailable, substitute only if the task permits it. When
identity is the claim, when a frozen evaluator requires a particular file, or
when the user selected that exact model or version, keep the boundary and route
the decision; do not silently change the comparison, the task objective or the
access assumptions.

Record the source or revision, the date and the result beside the configuration
or statement that needs them, and report the current condition, the decisive
evidence and the next action in the normal task response. Do not create an
open-ended watcher, an extra evidence file, or a repeated permission request for
an action already authorized.
