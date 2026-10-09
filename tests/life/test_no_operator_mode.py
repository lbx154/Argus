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
    assert "do not wait" in rendered
    # An assumption settles meaning only; it is recorded outside graded output.
    assert "Never assume facts, data, measurements, or results" in rendered
    assert "placeholder credentials or mocked services" in rendered
    assert "CHECKPOINT.md and the run report" in rendered
    # The assumption is recorded for the report and announced as an event.
    from argus.core.autonomy import read_autonomous_assumptions

    recorded = read_autonomous_assumptions(supervisor.memory.root)
    assert recorded and "unreleased gate" in recorded[-1]["conflict"]
    decided = [
        event for event in sink.events
        if event["type"] == EventType.LIFE_MANAGER_PLAN_CHALLENGE_DECIDED
    ]
    assert decided[-1]["autonomous_assumption"] is True


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
    # Reported as a block that needs the operator, not as idleness.
    assert events[-1]["type"] == EventType.LIFE_LIFECYCLE_BLOCK
    assert events[-1]["lifecycle_state"] == "needs_operator"
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


def test_web_started_bounded_worker_keeps_waiting_for_the_ui_answer(monkeypatch) -> None:
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    supervisor = SimpleNamespace(_pending_operator_questions=lambda: ["Which rule wins?"])
    worker = LifeWorkerRunMixin()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN", "1")
    worker._operator_wait_since = 0.0
    assert worker._bounded_operator_wait_expired([supervisor], enabled=False) is False


def test_operator_wait_exit_leaves_a_marker_for_answer_and_resume(
    tmp_path, monkeypatch
) -> None:
    from argus.core.autonomy import OPERATOR_WAIT_EXIT_FILENAME
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    supervisor = SimpleNamespace(
        _pending_operator_questions=lambda: ["Which rule wins?"],
        memory=SimpleNamespace(root=tmp_path),
    )
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert LifeWorkerRunMixin()._bounded_operator_wait_expired([supervisor]) is True
    marker = tmp_path / OPERATOR_WAIT_EXIT_FILENAME
    assert "blocked: needs operator" in marker.read_text(encoding="utf-8")


def test_cli_operator_wait_exit_is_on_only_for_foreground_bounded_runs() -> None:
    from argus.apps.cli import build_parser
    from argus.apps.cli._core import _operator_wait_exit_enabled

    parse = build_parser().parse_args
    assert _operator_wait_exit_enabled(parse(["--daemon-fg", "--bounded"])) is True
    assert _operator_wait_exit_enabled(parse(["--daemon", "--bounded"])) is False
    assert _operator_wait_exit_enabled(parse(["--daemon-fg"])) is False
    assert _operator_wait_exit_enabled(
        parse(["--daemon-fg", "--bounded", "--operator-wait-exit", "off"])
    ) is False


def test_spawned_worker_command_carries_the_operator_wait_choice(tmp_path) -> None:
    from argus.daemon.config import LifeWorkerConfig, config_from_payload, config_payload
    from argus.daemon.process import _windows_daemon_command

    web = LifeWorkerConfig(life_dir=tmp_path / "projects" / "s-1", continuous_open_ended=False)
    command = _windows_daemon_command(web)
    assert command[command.index("--operator-wait-exit") + 1] == "off"
    headless = LifeWorkerConfig(
        life_dir=tmp_path / "projects" / "s-2",
        continuous_open_ended=False,
        operator_wait_exit=True,
    )
    assert config_from_payload(config_payload(headless)).operator_wait_exit is True


def test_answer_restarts_a_worker_that_ended_on_the_question(tmp_path, monkeypatch) -> None:
    from argus.apps.cli import _core
    from argus.core.autonomy import OPERATOR_WAIT_EXIT_FILENAME

    (tmp_path / OPERATOR_WAIT_EXIT_FILENAME).write_text("{}", encoding="utf-8")
    started: list[str] = []
    monkeypatch.setattr(
        "argus.webapi.daemon_lifecycle.start_project_daemon",
        lambda sid, **_kwargs: started.append(sid) or {"rc": 0},
    )
    bundle = SimpleNamespace(
        project=SimpleNamespace(root=tmp_path, fingerprint="s-abc"),
        global_root=tmp_path,
    )
    _core._restart_worker_after_operator_wait_exit(bundle)
    assert started == ["s-abc"]
    assert not (tmp_path / OPERATOR_WAIT_EXIT_FILENAME).exists()
    # Without the marker the worker is left alone.
    _core._restart_worker_after_operator_wait_exit(bundle)
    assert started == ["s-abc"]


def test_no_operator_persists_across_resume(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import (
        adopt_persisted_operator_availability,
        operator_available,
        persist_operator_availability,
    )

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    persist_operator_availability(tmp_path)
    monkeypatch.delenv("ARGUS_SKILL_OPERATOR_AVAILABLE")
    assert operator_available() is True
    assert adopt_persisted_operator_availability(tmp_path) is False
    assert operator_available() is False
    # An explicit value in the resuming process wins over the record.
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert adopt_persisted_operator_availability(tmp_path) is None
    assert operator_available() is True


def test_mislabeled_technical_replan_cannot_force_push_without_the_operator(
    monkeypatch,
) -> None:
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    decision = adjudicate_plan_challenge(
        {
            "authority_impact": "technical",
            "challenge": "The release branch carries a broken commit.",
            "alternative": "Force-push the protected release branch without it.",
        },
        reviewer_status="replan_requested",
    )
    assert decision.action == "ask_operator"
    assert decision.operator_need == "irreversible_or_external"
    assert decision.source == "operator_only_action_backstop"
    # A technical alternative with no such action still replaces the plan.
    assert adjudicate_plan_challenge(
        {"authority_impact": "technical", "alternative": "Rebase the local branch."},
        reviewer_status="replan_requested",
    ).action == "replace"


def test_mislabeled_action_replan_blocks_without_an_operator(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, sink = _supervisor(tmp_path, runner=object())
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Ship the fix", objective="ship the fix")
    )
    outcome = _conflict_outcome(
        item.id,
        authority_impact="technical",
        alternative="Publish the package to PyPI now.",
    )
    action = supervisor._adjudicate_mission_challenge(outcome)
    assert action == "blocked"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert "irreversible_or_external" in stored.last_error


def test_reviewer_decision_request_carries_its_classification() -> None:
    from argus.reviewer.tools import ReviewActions

    tools = ReviewActions()
    schema = None
    for tool in tools.tools:
        if tool["name"] == "request_review_decision":
            schema = tool["inputSchema"]
    assert schema is not None
    assert schema["properties"]["operator_need"]["enum"] == [
        "credentials", "spending", "irreversible_or_external", "scope_or_authority", "none",
    ]
    tools.dispatch(
        "request_review_decision",
        {
            "review": "The deployment needs the staging key.",
            "question": "Provide the staging deployment key?",
            "operator_need": "credentials",
        },
    )
    assert tools.decision.planner_report["operator_need"] == "credentials"


@pytest.mark.parametrize("need", ["credentials", "spending", "irreversible_or_external"])
def test_no_operator_reviewer_action_need_blocks_the_round(tmp_path, monkeypatch, need) -> None:
    from argus.core.models import ReviewDecision
    from argus.engineer.round_settlement import _enforce_operator_question_policy

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    review = ReviewDecision(
        status="blocked",
        reason="needs an operator",
        next_action="",
        operator_question="Provide the production key?",
        planner_report={"operator_need": need},
    )
    config = SimpleNamespace(operator_question_policy_root=None, operator_questions_allowed=True)
    settled = _enforce_operator_question_policy(
        review, supervised_config=config, state=SimpleNamespace(rounds=[])
    )
    assert settled.status == "blocked"
    assert settled.operator_question == ""
    assert need in settled.reason
    assert settled.planner_report["operator_need"] == need


def test_no_operator_planner_wait_is_settled_before_the_run_ends(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import read_autonomous_assumptions
    from argus.life.supervisor._constants import PLAN_RETRY
    from argus.planner import PlannerVerdict, WaitingContract

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, sink = _supervisor(tmp_path, runner=object())
    verdict = PlannerVerdict(
        project_done=False,
        reason="the operator decides which release rule wins",
        waiting=True,
        waiting_reason="the operator decides which release rule wins",
        waiting_contract=WaitingContract(
            blocker_fingerprint="release-rule-conflict",
            recheck_condition="the operator decides which release rule wins",
            recheck_token="release-rule-conflict",
            wait_mode="event",
            wake_on=("authorization",),
            operator_action_required=True,
        ),
    )

    assert supervisor._record_planner_waiting(verdict) == PLAN_RETRY
    feedback = supervisor._load_manager_planner_feedback()
    assert feedback is not None and "no operator is available" in feedback["reason"]
    assert read_autonomous_assumptions(supervisor.memory.root)[-1]["source"] == "planner_wait"
    assert supervisor._pending_operator_questions() is None
    # The same blocker again is recorded as a wait, and the run may then end.
    assert supervisor._record_planner_waiting(verdict) != PLAN_RETRY
    assert supervisor._pending_operator_questions() == [
        "the operator decides which release rule wins"
    ]


def test_engineer_prompt_hides_the_operator_handoff_without_an_operator(monkeypatch) -> None:
    from argus.roles.prompts.engineer import engineer_operator_handoff_rule

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert "never use next_owner=operator" in engineer_operator_handoff_rule()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    rule = engineer_operator_handoff_rule()
    assert "OPERATOR_NEED=credentials (or" in rule
    assert "credentials|" not in rule


def test_bounded_completion_summary_names_the_assumptions(tmp_path) -> None:
    from argus.core.autonomy import record_autonomous_assumption, render_autonomous_assumptions

    record_autonomous_assumption(
        tmp_path, item_id="x", conflict="Two release rules conflict.", source="mission_challenge"
    )
    text = render_autonomous_assumptions(tmp_path)
    assert text.startswith("Decided without an operator")
    assert "Two release rules conflict." in text


def test_autonomous_mode_still_parks_a_reviewer_credential_request(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_AUTONOMY_MODE", "autonomous")
    supervisor, _sink = _supervisor(tmp_path, runner=_ClassifiedQuestionRunner("credentials"))
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Deploy staging", objective="deploy staging")
    )

    supervisor.tick()

    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
