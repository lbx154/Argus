# Stable acceptance and review receipts

A report cannot contain the newly generated, version-bound receipt of its own
current independent review: writing that receipt into the report changes the
reviewed artifact. Requiring another current receipt creates the same dependency
again. Repeating a review or marking the work complete does not fix this contract.

Before a queued mission executes, Argus checks narrowly identified contracts that
ask a report or document to embed current review/closeout evidence. The existing
Planner backend interprets the original objective, acceptance check, and non-goals
with tools disabled. It returns explicit artifact/receipt/subject relationships,
their placement and freshness, and verbatim supporting clauses. The host checks
that quoted clauses and named artifact paths occur in the current contract, then
detects cycles in those explicit relationships. The Planner interprets their meaning;
the host does not independently prove every natural-language interpretation.
Text matching only selects candidates;
it cannot declare a cycle or a failed task.

A confirmed cycle pauses the mission with an acceptance clarification card. An
ambiguous or malformed interpretation also pauses for clarification, explicitly
without claiming that a cycle was proven. The original acceptance is retained.
Neither path declares success, invents a review, or executes repeated repair/review
rounds. The operator can clarify whether the current receipt is evaluated externally
or an embedded receipt refers to a fixed prior review. Argus applies no such change
on the operator's behalf.

The dependency assessment is stored in the task's `manager_decision` under
`acceptance_dependency_assessment`, bound to the complete supplied mission contract.
Restarting or retrying that unchanged contract reuses the assessment. Changing a
contract field invalidates it. Normal artifact edits do not invalidate a structurally
valid acceptance contract and still receive the required independent review. If
the contract changes while the assessment runs, an atomic check discards the old
result and releases its claim; the next scheduling tick handles the updated task.
Concurrent Manager metadata and already-paused or terminated tasks are preserved.
Provider outages, budget limits, cancellation, and cooldowns retain their existing
stop classification; they are not cached as contract findings.

External current receipts, historical review citations, and receipts for other
artifacts can have stable completion conditions and are allowed when their explicit
dependency graph is acyclic. This guard does not attempt to interpret every natural
language condition, and cannot see an outer monitor's rule unless that rule is
included in the mission contract. Ambiguous cases require a concrete clarification;
there is no fixed review-count threshold or silent relaxation of acceptance.
