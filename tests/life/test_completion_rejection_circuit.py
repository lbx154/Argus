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

import json
from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.supervisor._constants import (
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)
from argus_skill.life.supervisor._planning_cycle_helpers import (
    _PlanCycleState,
    completion_rejection_circuit_path,
    load_completion_rejection_circuit,
    pause_completion_rejection_circuit,
    record_completion_rejection,
)
from argus_skill.life.supervisor._planning_cycle_intake import (
    PlanningCycleIntakeMixin,
)
from argus_skill.planner import PlannerVerdict

_OBJECTIVE = "finish the paper"


class _RejectionHarness(PlanningCycleCompletionMixin):
    """Just enough supervisor for the project_done normalization phase."""

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

    def _enter_idle_backoff(self) -> float:
        self.idle_backoffs += 1
        return 15.0


def _done_state() -> _PlanCycleState:
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        waiting=False,
        new_tasks=[],
        reason="The paper is complete.",
    )
    return state


def _circuit(tmp_path: Path) -> dict | None:
    return load_completion_rejection_circuit(
        completion_rejection_circuit_path(tmp_path, _OBJECTIVE)
    )


# --------------------------------------------------------------------------- #
# Trip at three identical turn-backs
# --------------------------------------------------------------------------- #


def test_third_identical_turn_back_pauses_and_tells_the_operator(tmp_path) -> None:
    harness = _RejectionHarness(tmp_path)

    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert harness._pc_normalize_project_done(_done_state()) == PLAN_TERMINAL_IDLE

    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["paused"] is True
    assert circuit["consecutive_rejections"] == 3
    assert circuit["diagnostic"] == "final_certification_missing"
    assert circuit["pause_backlog_signature"] == "backlog:one"

    opened = [
        event
        for event in harness.events
        if event["type"] == "life.planner.completion_circuit_opened"
    ]
    assert len(opened) == 1
    assert opened[0]["operator_alert"] is True
    assert opened[0]["consecutive_rejections"] == 3

    # The operator hears about it as one plain-language message in the
    # project transcript, not only as a machine event.
    transcript_texts = [
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.jsonl")
    ]
    assert any("times in a row" in text for text in transcript_texts)

    # The paused turn does NOT reset the idle backoff (that reset is what
    # kept the loop cycling at full speed); it enters backoff instead.
    assert harness.idle_backoffs == 1
    assert harness.idle_resets == 2


def test_two_turn_backs_then_a_different_reason_do_not_pause(tmp_path) -> None:
    harness = _RejectionHarness(tmp_path)

    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["consecutive_rejections"] == 2
    assert circuit["paused"] is False

    # The certification lands (the recovery fix in action), so the next
    # completion attempt fails for a DIFFERENT reason: the count restarts.
    harness.certified = True
    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY

    circuit = _circuit(tmp_path)
    assert circuit is not None
    assert circuit["consecutive_rejections"] == 1
    assert circuit["paused"] is False
    assert circuit["diagnostic"] == "manager_final_stage_not_completed"
    assert not [
        event
        for event in harness.events
        if event["type"] == "life.planner.completion_circuit_opened"
    ]


def test_completion_that_goes_through_clears_the_history(tmp_path) -> None:
    harness = _RejectionHarness(tmp_path)

    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert harness._pc_normalize_project_done(_done_state()) == PLAN_RETRY
    assert _circuit(tmp_path) is not None

    # The recovery path resolves the cause before a third turn-back: the
    # completion goes through and the stop-loss never fires.
    harness.certified = True
    harness.final_stage_completed = True
    assert harness._pc_normalize_project_done(_done_state()) is False
    assert harness.planner_verdicts == 1
    assert _circuit(tmp_path) is None
    assert not [
        event
        for event in harness.events
        if event["type"] == "life.planner.completion_circuit_opened"
    ]


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

    def _deactivate_planner_waiting_contract(self) -> None:
        pass

    def _emit(self, event) -> None:
        self.events.append(dict(event))

    def _emit_status(self, text: str) -> None:
        self.statuses.append(str(text))

    def _reset_idle_backoff(self) -> None:
        self.idle_resets += 1

    def _enter_idle_backoff(self) -> float:
        self.idle_holds += 1
        return 30.0

    def _retry_pending_planner_verdict(self):
        # Sentinel: reaching this means the hold let the cycle continue.
        return True, "reached-the-planner"


def _paused_circuit(tmp_path: Path, backlog_signature: str) -> Path:
    path = completion_rejection_circuit_path(tmp_path, _OBJECTIVE)
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


def test_circuit_state_survives_a_restart(tmp_path) -> None:
    """The count lives on disk, not in the process (consecutive_replans
    precedent): a daemon restart must not grant three fresh paid cycles."""
    path = completion_rejection_circuit_path(tmp_path, _OBJECTIVE)
    record_completion_rejection(
        path, diagnostic="research_target_incomplete", reason="same cause"
    )
    record_completion_rejection(
        path, diagnostic="research_target_incomplete", reason="same cause"
    )

    # A "restart": nothing in memory, only the file.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["consecutive_rejections"] == 2

    record = record_completion_rejection(
        path, diagnostic="research_target_incomplete", reason="same cause"
    )
    assert record["consecutive_rejections"] == 3
