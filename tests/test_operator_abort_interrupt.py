"""Operator-abort interrupt: the Manager (running in the operator's REPL, a
separate process from the daemon) can decide, mid-mission, that *this one*
backlog item should stop right now — without tearing down the daemon
process itself (contrast with ``fatal_error_looks_like_daemon_stop_request``,
which is a full daemon-shutdown interrupt).

The signal travels as a small file in the shared ``life_dir``; the running round's
``external_interrupt_reason_provider`` watchdog consumes it and the engineer
subprocess is terminated with ``fatal_error="External interrupt: operator
abort requested: <reason>"``. This must be classified as its OWN category —
never as a retryable backend failure, and never silently laundered into an
infinite retry loop.
"""
from __future__ import annotations

from argus.engineer.runner import (
    operator_abort_review_decision,
)

# --------------------------------------------------------------------------- #
# Pure predicate: distinct from both "normal backend failure" and "daemon stop"
# --------------------------------------------------------------------------- #


def test_operator_abort_review_decision_is_honest_daemon_keeps_running() -> None:
    decision = operator_abort_review_decision(
        fatal_error="External interrupt: operator abort requested: stop now",
        exit_code=0,
    )
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "operator_abort"
    assert decision.engineer_aborted_before_review is False
    assert decision.reason == "The operator requested this mission be aborted."
    assert "Manager decided" not in decision.reason
    # Must NOT claim the daemon itself is restarting/shutting down — only
    # this one mission was interrupted (regression guard against copy-pasting
    # daemon_stop_review_decision's "restart the daemon" wording verbatim,
    # which would misinform the operator that the whole daemon is bouncing).
    assert "daemon" not in decision.reason.lower()
    assert "restart the daemon" not in decision.next_action.lower()
    assert "daemon process itself keeps running" in decision.next_action.lower()
    assert "next" in decision.next_action.lower() or "ready backlog" in decision.next_action.lower()


# --------------------------------------------------------------------------- #
# Round-loop integration: the engineer subprocess was killed mid-turn by the
# watchdog because the Manager requested an abort.
# --------------------------------------------------------------------------- #
