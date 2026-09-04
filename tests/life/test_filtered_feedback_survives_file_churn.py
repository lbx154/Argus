"""Filtered-task planner feedback must outlive background-job file churn.

Regression for the run-08 replan loop: while frozen GPU workers and their
finalizer kept rewriting project files, the whole-tree evidence signature
changed every cycle, so "all proposed tasks were filtered" feedback was
cleared within one cycle, the idle backoff reset to base, and the planner
made a full model call roughly every backoff period indefinitely. Feedback
with the filtered diagnostic is now judged against the backlog's own state:
it persists (and its attempt count climbs toward the repeat limit) until a
backlog item actually changes status.
"""

from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.supervisor._constants import (
    PLAN_ERROR,
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
)
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)


class _Harness(PlanningContextMixin):
    def __init__(self, tmp_path, backlog_rows) -> None:
        self.config = SimpleNamespace(
            project_state_dir=str(tmp_path),
            continuous_objective="confirm the frozen Route 09 evidence",
        )
        self.backlog_rows = list(backlog_rows)
        self.memory = SimpleNamespace(
            root=str(tmp_path),
            backlog=SimpleNamespace(active=lambda: list(self.backlog_rows)),
        )
        self._tree_churn = 0

    def _manager_feedback_evidence_signature(self) -> str:
        # Simulates live background jobs rewriting project files: the
        # whole-tree signature never repeats. The filtered diagnostic must
        # not consult it at all.
        self._tree_churn += 1
        return f"tree-{self._tree_churn}"


def test_filtered_feedback_survives_project_file_churn(tmp_path) -> None:
    harness = _Harness(
        tmp_path,
        [
            SimpleNamespace(id="adjudicate-route09", status="pending"),
            SimpleNamespace(id="confirm-route09", status="paused_external_work"),
        ],
    )

    # Three consecutive all-filtered verdicts. The planner rephrases the
    # duplicate title each cycle and the project tree churns underneath,
    # but the backlog is unchanged — attempts must accumulate, not reset.
    reasons = [
        "[duplicate_task] Adjudicate the frozen Route 09 confirmation",
        "[duplicate_task] Adjudicate the completed Route 09 confirmation",
        "[duplicate_task] Adjudicate Route 09 confirmation evidence",
    ]
    feedback = None
    for expected_attempts, reason in enumerate(reasons, start=1):
        assert harness._persist_manager_planner_feedback(
            stage="experiment",
            reason=reason,
            diagnostic=PLANNER_TASKS_FILTERED_DIAGNOSTIC,
        )
        feedback = harness._load_manager_planner_feedback()
        assert feedback is not None
        assert int(feedback["attempts"]) == expected_attempts

    # Intake compares the recorded signature against the same backlog-based
    # signature, so an unchanged backlog keeps the feedback alive.
    recorded = str(feedback["evidence_signature"])
    assert recorded == harness._manager_feedback_signature_for(
        PLANNER_TASKS_FILTERED_DIAGNOSTIC
    )

    # The background job settles and the paused mission resumes: the backlog
    # signature moves, which is exactly what wakes the planner back up.
    harness.backlog_rows[1].status = "running"
    assert (
        harness._manager_feedback_signature_for(PLANNER_TASKS_FILTERED_DIAGNOSTIC)
        != recorded
    )


class _WaitingHarness(_Harness, PlanningCycleCompletionMixin):
    """Enough of the supervisor to run the waiting-verdict feedback gate."""

    def __init__(self, tmp_path, backlog_rows) -> None:
        _Harness.__init__(self, tmp_path, backlog_rows)
        self.emitted: list[dict] = []
        self.backoff_entries = 0

    def _emit(self, event) -> None:
        self.emitted.append(dict(event))

    def _emit_status(self, message: str) -> None:
        self.emitted.append({"type": "status", "message": message})

    def _enter_idle_backoff(self) -> None:
        self.backoff_entries += 1

    def _reconcile_open_ended_planner_waiting(self, verdict) -> bool:
        return False

    def _record_planner_waiting(self, verdict):
        return "waiting-recorded"

    def _maybe_dispatch_verification_probe(self, verdict) -> bool:
        return False


def _waiting_state():
    return SimpleNamespace(
        verdict=SimpleNamespace(waiting=True, waiting_reason="", reason=""),
        revision_request=None,
        expected_plan_id=None,
        expected_plan_version=None,
        revision_active_items=[],
    )


def test_waiting_verdict_resolves_filtered_feedback(tmp_path) -> None:
    """Regression for the run-08 planner deadlock: with filtered-task
    feedback active, every waiting verdict was rejected as "planner ignored
    unresolved Manager feedback", so the planner's correct answer (wait for
    the live finalizer) could never land and it replanned forever. A wait is
    the resolution of "everything you proposed already exists": the feedback
    must be cleared and the waiting contract installed."""
    harness = _WaitingHarness(
        tmp_path, [SimpleNamespace(id="confirm-route09", status="running")]
    )
    assert harness._persist_manager_planner_feedback(
        stage="experiment",
        reason="[duplicate_task] Adjudicate the frozen Route 09 confirmation",
        diagnostic=PLANNER_TASKS_FILTERED_DIAGNOSTIC,
    )

    result = harness._pc_handle_waiting(_waiting_state())

    assert result == "waiting-recorded"
    assert harness._load_manager_planner_feedback() is None
    assert harness.backoff_entries == 0


def test_waiting_verdict_still_rejected_for_other_feedback(tmp_path) -> None:
    harness = _WaitingHarness(
        tmp_path, [SimpleNamespace(id="confirm-route09", status="running")]
    )
    assert harness._persist_manager_planner_feedback(
        stage="experiment",
        reason="stage completion gate failed",
        diagnostic="stage_completion_gate_failed",
    )

    result = harness._pc_handle_waiting(_waiting_state())

    assert result == PLAN_ERROR
    assert harness._load_manager_planner_feedback() is not None
    assert harness.backoff_entries == 1
