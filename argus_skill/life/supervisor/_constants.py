"""Shared supervisor scope constants."""

import os

PLANNER_SCOPE_BOUNDED = "bounded"
PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"
IDLE_BACKOFF_BASE_SECONDS = 15.0
# This caps event-poll latency while idle; it never limits mission work.
IDLE_BACKOFF_CAP_SECONDS = 300.0
# How often the Planner is re-granted a turn while an operator-only wait
# holds. Each grant is a full Planner LLM call, so this is an LLM-call-rate
# policy, not a poll-latency bound: it is deliberately decoupled from
# IDLE_BACKOFF_CAP_SECONDS so retuning idle sleep never retunes token spend
# (magic-hyperparameters audit 2026-09-05).
OPERATOR_WAIT_TURN_REGRANT_SECONDS = 300.0
# These are fact-emission cadences so indefinite waits remain observable.
PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS = 1800.0
LIFECYCLE_BLOCK_HEARTBEAT_SECONDS = 1800.0
PLAN_TERMINAL_IDLE = "planner_terminal_idle"
PLAN_AWAITING = "awaiting_external"
PLAN_ERROR = "planner_error"
PLAN_RETRY = "planner_retry"
PLANNER_DEDUP_STATUSES = frozenset({
    "pending",
    "running",
    "paused",
    "paused_budget",
    "paused_provider_cooldown",
    "paused_provider_fence",
    "paused_daemon_shutdown",
    "paused_external_work",
    "paused_operator",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
    "infra_blocked",
    "done",
})
PLANNER_RECENT_FAILURE_STATUS = "no_progress"
CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD = 3
VERIFICATION_PROBE_AFTER_IDLE_CYCLES = 4
MANAGER_RECONCILE_AFTER_IDLE_CYCLES = 4
VERIFICATION_PROBE_COOLDOWN_SECONDS = 1800.0
STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS = 3
REPLAN_FILTER_REJECTION_LIMIT = 3
MANAGER_FEEDBACK_REPLAN_LIMIT = 3
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


def consecutive_replan_escalation_threshold() -> int:
    """Consecutive replan_requested outcomes on one item before escalation.

    A refuted node that keeps returning ``replan_requested`` with no forward
    progress must not be re-dispatched forever. At/above this many consecutive
    replans (counting the in-flight one) the supervisor stops resetting the
    item to pending and escalates it to a terminal no-progress failure.

    Env-overridable via ``ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD``;
    defaults to ``CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD``.
    """
    raw = os.environ.get(
        "ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD", ""
    ).strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD


__all__ = [
    "IDLE_BACKOFF_BASE_SECONDS",
    "IDLE_BACKOFF_CAP_SECONDS",
    "OPERATOR_WAIT_TURN_REGRANT_SECONDS",
    "PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS",
    "LIFECYCLE_BLOCK_HEARTBEAT_SECONDS",
    "PLANNER_SCOPE_BOUNDED",
    "PLANNER_SCOPE_FINAL_SUBMISSION",
    "PLAN_TERMINAL_IDLE",
    "PLAN_AWAITING",
    "PLAN_ERROR",
    "PLAN_RETRY",
    "PLANNER_DEDUP_STATUSES",
    "PLANNER_RECENT_FAILURE_STATUS",
    "CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD",
    "consecutive_replan_escalation_threshold",
    "VERIFICATION_PROBE_AFTER_IDLE_CYCLES",
    "MANAGER_RECONCILE_AFTER_IDLE_CYCLES",
    "VERIFICATION_PROBE_COOLDOWN_SECONDS",
    "STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS",
    "REPLAN_FILTER_REJECTION_LIMIT",
    "MANAGER_FEEDBACK_REPLAN_LIMIT",
    "FULL_PAPER_GATE_DESCRIPTION",
]
