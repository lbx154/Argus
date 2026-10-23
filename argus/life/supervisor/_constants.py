"""Shared supervisor scope constants."""

PLANNER_SCOPE_BOUNDED = "bounded"
PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"
IDLE_BACKOFF_BASE_SECONDS = 15.0
# This caps event-poll latency while idle; it never limits mission work.
IDLE_BACKOFF_CAP_SECONDS = 300.0
# These are fact-emission cadences so indefinite waits remain observable.
PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS = 1800.0
LIFECYCLE_BLOCK_HEARTBEAT_SECONDS = 1800.0
# Manager attempts at one parked card it decides in the operator's place. After
# the last the card waits, with a notice, until it changes or the operator returns.
PROXY_DECISION_ATTEMPTS = 3
PLAN_TERMINAL_IDLE = "planner_terminal_idle"
PLAN_AWAITING = "awaiting_external"
PLAN_ERROR = "planner_error"
PLAN_RETRY = "planner_retry"
PLANNER_DEDUP_STATUSES = frozenset({"pending", "running", "parked"})
MANAGER_FEEDBACK_REPLAN_LIMIT = 3
# Consecutive planner completion attempts turned back for the SAME reason
# before the supervisor stops asking and tells the operator. This is keyed on
# the rejection reason alone (not the project evidence signature), because a
# requirement the Planner cannot satisfy survives any amount of file churn:
# one live project burned 58 identical turn-backs in 48 hours before this
# stop-loss existed. Persisted next to the backlog state so a daemon restart
# does not reset the count.
COMPLETION_REJECTION_CIRCUIT_THRESHOLD = 3
# Consecutive provider-cooldown pauses of one mission with the SAME normalized
# error before a model-configuration failure is treated as permanent (a wrong
# model name, not a provider blip) and the mission is parked on the operator
# instead of being resumed and re-billed forever.
PROVIDER_COOLDOWN_SAME_CAUSE_LIMIT = 3
# Diagnostic recorded when every task in a planner verdict was filtered
# (duplicates of existing backlog items, etc.). Feedback with this diagnostic
# is validated against the backlog's own state, not the project-file evidence
# signature: live background jobs rewrite project files constantly, and that
# churn says nothing about whether a fresh plan would stop duplicating the
# backlog.
PLANNER_TASKS_FILTERED_DIAGNOSTIC = "planner_tasks_filtered"
FULL_PAPER_GATE_DESCRIPTION = (
    "the L2 reviewer's active vertical final-stage checklist"
)


__all__ = [
    "IDLE_BACKOFF_BASE_SECONDS",
    "IDLE_BACKOFF_CAP_SECONDS",
    "PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS",
    "LIFECYCLE_BLOCK_HEARTBEAT_SECONDS",
    "PLANNER_SCOPE_BOUNDED",
    "PLANNER_SCOPE_FINAL_SUBMISSION",
    "PLAN_TERMINAL_IDLE",
    "PLAN_AWAITING",
    "PLAN_ERROR",
    "PLAN_RETRY",
    "PLANNER_DEDUP_STATUSES",
    "MANAGER_FEEDBACK_REPLAN_LIMIT",
    "COMPLETION_REJECTION_CIRCUIT_THRESHOLD",
    "PROVIDER_COOLDOWN_SAME_CAUSE_LIMIT",
    "FULL_PAPER_GATE_DESCRIPTION",
]
