"""Stop-loss for the completion loop nobody can win.

Regression for the 48-hour window in which one project produced 58 identical
completion turn-backs (missing_publishable_reviewer_certification, ~$357): the
Planner kept declaring the project done, the standing requirement kept turning
it back for the same reason, and every cycle was a paid model call reproducing
the same exchange. After three consecutive same-reason turn-backs the
supervisor now tells the operator in plain language and pauses completion
attempts; the pause lifts when the backlog changes or the operator replies.

The certification-recovery fix removes one specific cause of those turn-backs;
this stop-loss is the general backstop. The two must coexist: turn-backs that
stop occurring (because recovery resolved the cause) never reach the
threshold, and a completion that goes through clears the count entirely.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.life.supervisor._constants import (
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus.life.supervisor._planning_context import PlanningContextMixin
from argus.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)
from argus.life.supervisor._planning_cycle_helpers import (
    _PlanCycleState,
    completion_rejection_circuit_path,
    load_completion_rejection_circuit,
    pause_completion_rejection_circuit,
    record_completion_rejection,
)
from argus.life.supervisor._planning_cycle_intake import (
    PlanningCycleIntakeMixin,
)
from argus.planner import PlannerVerdict

_OBJECTIVE = "finish the paper"


class _RejectionHarness(PlanningCycleCompletionMixin):
    """Just enough supervisor for the project_done normalization phase."""

    from argus.life.supervisor._core import LifeSupervisor

    _operator_chinese = LifeSupervisor._operator_chinese

    def __init__(self, tmp_path: Path) -> None:
        self.root = Path(tmp_path)
        self.config = SimpleNamespace(
            open_ended=False,
            continuous_objective=_OBJECTIVE,
        )
        self.memory = SimpleNamespace(
            root=Path(tmp_path),
            journal=SimpleNamespace(all=lambda: []),
        )
        self.events: list[dict] = []
        self.statuses: list[str] = []
        self.idle_backoffs = 0
        self.idle_resets = 0
        self.backlog_signature = "backlog:one"
        self.certified = False
        self.final_stage_completed = False
        self.planner_verdicts = 0
        self._planning_cycles = 1

    def _artifact_root(self):
        return self.root

    def _project_workdir(self):
        return self.root

    def _project_state_root(self):
        return self.root

    def _current_pipeline_stage(self) -> str:
        return "submission"

    def _effective_final_certification_gate(self, _root) -> bool:
        return True

    def _journal_has_final_certification(self) -> bool:
        return self.certified

    def _final_submission_signature(self) -> str:
        return ""

    def _persist_manager_planner_feedback(self, **_payload) -> bool:
        return True

    def _clear_manager_planner_feedback(self) -> None:
        pass

    def _backlog_planning_signature(self) -> str:
        return self.backlog_signature

    def _manager_final_stage_is_completed(self) -> bool:
        return self.final_stage_completed

    def _bounded_request_accepted(self) -> str:
        return ""

    def _open_ended_terminal_idle_signature(self) -> str:
        return ""

    def _emit_planner_verdict(self, **_kwargs) -> bool:
        self.planner_verdicts += 1
        return True

    def _emit(self, event) -> None:
        self.events.append(dict(event))

    def _emit_status(self, text: str) -> None:
        self.statuses.append(str(text))

    def _reset_idle_backoff(self) -> None:
        self.idle_resets += 1

    def _enter_idle_backoff(self) -> None:
        self.idle_backoffs += 1


def _done_state() -> _PlanCycleState:
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        new_tasks=[],
        summary="The paper is complete.",
    )
    return state


def _circuit(tmp_path: Path) -> dict | None:
    return load_completion_rejection_circuit(
        completion_rejection_circuit_path(tmp_path)
    )


# --------------------------------------------------------------------------- #
# Trip at three identical turn-backs
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The intake phase holds the pause and lifts it on the two wake conditions
# --------------------------------------------------------------------------- #


class _IntakeHarness(PlanningContextMixin, PlanningCycleIntakeMixin):
    """Enough of the supervisor to run the intake hold."""

    def __init__(self, tmp_path: Path, backlog_rows) -> None:
        self.config = SimpleNamespace(
            project_state_dir=str(tmp_path),
            continuous_objective=_OBJECTIVE,
        )
        self.backlog_rows = list(backlog_rows)
        self.memory = SimpleNamespace(
            root=str(tmp_path),
            backlog=SimpleNamespace(active=lambda: list(self.backlog_rows)),
        )
        self.events: list[dict] = []
        self.statuses: list[str] = []
        self.idle_holds = 0
        self.idle_resets = 0
        self.inbox: list[str] = []

    def _take_operator_guidance_carryover(self) -> list[str]:
        return []

    def _drain_user_inbox(self) -> list[str]:
        drained, self.inbox = list(self.inbox), []
        return drained


    def _emit(self, event) -> None:
        self.events.append(dict(event))

    def _emit_status(self, text: str) -> None:
        self.statuses.append(str(text))

    def _reset_idle_backoff(self) -> None:
        self.idle_resets += 1

    def _enter_idle_backoff(self) -> None:
        self.idle_holds += 1

    def _retry_pending_planner_verdict(self):
        # Sentinel: reaching this means the hold let the cycle continue.
        return True, "reached-the-planner"


def _paused_circuit(tmp_path: Path, backlog_signature: str) -> Path:
    path = completion_rejection_circuit_path(tmp_path)
    for _ in range(3):
        record_completion_rejection(
            path,
            diagnostic="research_target_incomplete",
            reason="missing_publishable_reviewer_certification",
        )
    pause_completion_rejection_circuit(path, backlog_signature=backlog_signature)
    return path


def test_unchanged_backlog_holds_without_a_planner_call(tmp_path) -> None:
    rows = [SimpleNamespace(id="write-paper", status="pending")]
    harness = _IntakeHarness(tmp_path, rows)
    _paused_circuit(tmp_path, harness._backlog_planning_signature())

    result = harness._pc_intake_gate(_PlanCycleState(None))

    assert result == PLAN_TERMINAL_IDLE
    assert harness.idle_holds == 1
    holding = [
        event
        for event in harness.events
        if event["type"] == "life.planner.completion_circuit_holding"
    ]
    assert len(holding) == 1
    assert holding[0]["consecutive_rejections"] == 3
    circuit = _circuit(tmp_path)
    assert circuit is not None and circuit["paused"] is True


def test_backlog_change_lifts_the_pause_and_an_identical_turn_back_retrips(
    tmp_path,
) -> None:
    # Three identical turn-backs pause the loop.
    rejection = _RejectionHarness(tmp_path)
    assert rejection._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert rejection._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert rejection._pc_normalize_project_done(_done_state()) == PLAN_TERMINAL_IDLE

    # The backlog moves while the pause holds (the intake harness's rows hash
    # to a different signature than the recorded "backlog:one"), so the next
    # intake lifts the pause and lets one planning cycle through.
    rows = [SimpleNamespace(id="write-paper", status="done")]
    intake = _IntakeHarness(tmp_path, rows)
    result = intake._pc_intake_gate(_PlanCycleState(None))

    assert result == "reached-the-planner"
    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["paused"] is False
    assert circuit["resume_reason"] == "backlog_changed"

    # The count survives the wake: ONE more identical turn-back re-pauses
    # immediately instead of granting three more paid cycles.
    assert rejection._pc_normalize_project_done(_done_state()) == PLAN_TERMINAL_IDLE
    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["paused"] is True
    assert circuit["consecutive_rejections"] == 4


def test_operator_reply_lifts_the_pause(tmp_path) -> None:
    rows = [SimpleNamespace(id="write-paper", status="pending")]
    harness = _IntakeHarness(tmp_path, rows)
    _paused_circuit(tmp_path, harness._backlog_planning_signature())
    harness.inbox = ["the certification requirement is wrong; drop it"]

    result = harness._pc_intake_gate(_PlanCycleState(None))

    assert result == "reached-the-planner"
    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["paused"] is False
    assert circuit["resume_reason"] == "operator_reply"
