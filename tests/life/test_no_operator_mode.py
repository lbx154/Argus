"""Runs with and without an operator settle operator decisions differently.

With an operator, a decision the raising role classified as the operator's is
asked. Without one, nobody is asked: scope and interpretation questions are
settled on the most defensible reading with the assumption recorded, and only
an action the operator alone can enable ends the work as blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.event_catalog import EventType
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._planning_cycle_helpers import _render_revision_request


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool = False
    status: str = "blocked"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "needs a decision"
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    research_result: dict[str, Any] | None = None


class _ClassifiedQuestionRunner:
    def __init__(self, need: str) -> None:
        self.need = need

    def execute(self, **_kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            operator_question="Which of the two conflicting release rules wins?",
        )
        outcome.final_review_reason = "Two requirements conflict."
        outcome.final_review_next_action = ""
        outcome.final_planner_report = {
            "forward_progress": False,
            "plan_signal": "reconsider",
            "authority_impact": "operator",
            "operator_need": self.need,
        }
        return outcome


def _supervisor(tmp_path, runner: Any) -> tuple[LifeSupervisor, _Sink]:
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"),
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    return supervisor, sink


def _conflict_outcome(item_id: str, **report: Any) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "status": "replan_requested",
        "review_status": "replan_requested",
        "review_reason": "The mandatory work order is held by an unreleased gate.",
        "planner_report": {
            "plan_signal": "reconsider",
            "challenge": "The mandatory work order is held by an unreleased gate.",
            "authority_impact": "operator",
            **report,
        },
    }


def test_operator_available_still_asks_for_an_operator_owned_conflict(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    supervisor, sink = _supervisor(tmp_path, runner=object())
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )

    action = supervisor._adjudicate_mission_challenge(_conflict_outcome(item.id))

    assert action == "ask_operator"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.pending_question
    assert any(
        event["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING
        for event in sink.events
    )


def test_no_operator_settles_the_conflict_on_a_recorded_assumption(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, sink = _supervisor(tmp_path, runner=object())
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )
    outcome = _conflict_outcome(item.id)

    action = supervisor._adjudicate_mission_challenge(outcome)

    assert action == "revise"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status != "paused_operator"
    assert not stored.pending_question
    assert not any(
        event["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING
        for event in sink.events
    )
    challenge = outcome["plan_challenge"]
    assert challenge["autonomous_assumption"] is True
    assert "No operator is available" in challenge["manager_reason"]
    # The Planner is told to decide, record the assumption, and not wait.
    rendered = _render_revision_request(outcome, [])
    assert "manager_instruction:" in rendered
    assert "explicit assumption" in rendered
    assert "Do not wait" in rendered


@pytest.mark.parametrize("need", ["credentials", "spending", "irreversible_or_external"])
def test_no_operator_blocks_an_action_only_the_operator_can_enable(
    tmp_path, monkeypatch, need
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, _sink = _supervisor(tmp_path, runner=object())
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Publish the build", objective="publish the build")
    )

    action = supervisor._adjudicate_mission_challenge(
        _conflict_outcome(item.id, operator_need=need)
    )

    assert action == "blocked"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert stored.pending_question == ""
    assert need in stored.last_error


def test_no_operator_mission_question_becomes_a_replan_not_a_park(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, sink = _supervisor(
        tmp_path, runner=_ClassifiedQuestionRunner("scope_or_authority")
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "replan_requested"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status != "paused_operator"
    assert stored.pending_question == ""
    assert not any(
        event["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING
        for event in sink.events
    )


def test_operator_available_mission_question_still_parks(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    supervisor, _sink = _supervisor(
        tmp_path, runner=_ClassifiedQuestionRunner("scope_or_authority")
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )

    supervisor.tick()

    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.pending_question == "Which of the two conflicting release rules wins?"


def test_pending_operator_questions_reports_only_an_operator_wait(tmp_path) -> None:
    supervisor, _sink = _supervisor(tmp_path, runner=object())
    backlog = supervisor.memory.backlog
    parked = backlog.add(BacklogItem.new(title="Plan", objective="plan"))
    backlog.update(parked.id, status="paused_operator", pending_question="Which rule wins?")
    blocked_child = BacklogItem.new(title="Write back", objective="write back")
    blocked_child.deps = [parked.id]
    backlog.add(blocked_child)

    assert supervisor._pending_operator_questions() == ["Which rule wins?"]

    backlog.add(BacklogItem.new(title="Independent", objective="independent work"))
    assert supervisor._pending_operator_questions() is None


def test_bounded_daemon_ends_an_operator_only_wait_with_a_clear_outcome(
    monkeypatch,
) -> None:
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    events: list[dict[str, Any]] = []
    statuses: list[str] = []
    supervisor = SimpleNamespace(
        _pending_operator_questions=lambda: ["Which rule wins?"],
        _emit=events.append,
        _emit_status=statuses.append,
    )
    worker = LifeWorkerRunMixin()

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN", "30")
    assert worker._bounded_operator_wait_expired([supervisor]) is False
    assert events == []
    # The grace is measured from the first pass that found only the wait.
    worker._operator_wait_since -= 31 * 60
    assert worker._bounded_operator_wait_expired([supervisor]) is True
    assert events[-1]["type"] == EventType.LIFE_DAEMON_IDLE_TIMEOUT
    assert events[-1]["text"].startswith("blocked: needs operator")
    assert "Which rule wins?" in events[-1]["text"]
    assert statuses and statuses[-1].startswith("blocked: needs operator")


def test_bounded_daemon_without_an_operator_does_not_wait_at_all(monkeypatch) -> None:
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    supervisor = SimpleNamespace(
        _pending_operator_questions=lambda: ["Which rule wins?"],
        _emit=lambda _event: None,
        _emit_status=lambda _text: None,
    )
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert LifeWorkerRunMixin()._bounded_operator_wait_expired([supervisor]) is True


def test_bounded_daemon_keeps_working_while_other_work_remains(monkeypatch) -> None:
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    supervisor = SimpleNamespace(_pending_operator_questions=lambda: None)
    worker = LifeWorkerRunMixin()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert worker._bounded_operator_wait_expired([supervisor]) is False
    assert worker._operator_wait_since is None


def test_operator_wait_can_be_made_indefinite(monkeypatch) -> None:
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    supervisor = SimpleNamespace(_pending_operator_questions=lambda: ["Which rule wins?"])
    worker = LifeWorkerRunMixin()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN", "0")
    assert worker._bounded_operator_wait_expired([supervisor]) is False
    worker._operator_wait_since -= 10 * 24 * 3600
    assert worker._bounded_operator_wait_expired([supervisor]) is False


def test_operator_context_tells_every_role_when_nobody_will_answer(
    tmp_path, monkeypatch
) -> None:
    from argus.core.operator_context import build_operator_context_block

    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    block, _revision = build_operator_context_block("planner", life)
    assert "no operator is available" in block
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    block, _revision = build_operator_context_block("planner", life)
    assert "no operator is available" not in block
