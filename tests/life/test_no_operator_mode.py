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


@pytest.fixture(autouse=True)
def _isolated_run_scope(monkeypatch):
    # Registered so any run id a test starts is restored afterwards.
    monkeypatch.setenv("ARGUS_AUTONOMY_RUN_ID", "test-run")


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


@pytest.mark.parametrize(
    ("label", "alternative", "expected"),
    [
        # Operator available: any label but none, or no label, asks.
        ("credentials", "Use the deployment account for the live check.", "ask_operator"),
        ("scope_or_authority", "Narrow the claim to the admissible cells.", "ask_operator"),
        (None, "Narrow the claim to the admissible cells.", "ask_operator"),
        ("none", "Narrow the claim to the admissible cells.", "replace"),
        # Defense in depth: an exact command form asks whatever the label.
        ("none", "Run `git push --force origin release` to drop the commit.", "ask_operator"),
        # Prose about force-pushing is not a command form.
        ("none", "Do not force-push; rebase the local branch instead.", "replace"),
    ],
)
def test_plan_alternative_routing_with_an_operator(monkeypatch, label, alternative, expected) -> None:
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    decision = adjudicate_plan_challenge(
        {"authority_impact": "technical", "challenge": "c", "alternative": alternative},
        reviewer_status="replan_requested",
        alternative_operator_need=label,
    )
    assert decision.action == expected


@pytest.mark.parametrize(
    ("label", "alternative", "expected"),
    [
        # No operator: a label means blocked.
        ("credentials", "Use the deployment account for the live check.", "blocked"),
        ("scope_or_authority", "Narrow the claim to the admissible cells.", "blocked"),
        # No label: blocked only when a command form fires, else continue.
        (None, "Narrow the claim to the admissible cells.", "replace"),
        (None, "Then `npm publish` the package.", "blocked"),
        ("none", "Then `terraform apply` the change.", "blocked"),
        ("none", "Narrow the claim to the admissible cells.", "replace"),
    ],
)
def test_plan_alternative_routing_without_an_operator(monkeypatch, label, alternative, expected) -> None:
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    decision = adjudicate_plan_challenge(
        {"authority_impact": "technical", "challenge": "c", "alternative": alternative},
        reviewer_status="replan_requested",
        alternative_operator_need=label,
    )
    assert decision.action == expected


def test_supervisor_asks_the_manager_to_classify_the_alternative(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, _sink = _supervisor(tmp_path, runner=object())
    asked: list[tuple[str, str]] = []

    def classify(challenge, alternative):
        asked.append((challenge, alternative))
        return "spending"

    monkeypatch.setattr(supervisor, "_classify_plan_alternative", classify)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Scale the run", objective="scale the run")
    )
    outcome = _conflict_outcome(
        item.id,
        authority_impact="technical",
        alternative="Move the sweep to the larger cluster tier.",
    )
    action = supervisor._adjudicate_mission_challenge(outcome)
    assert asked and asked[0][1] == "Move the sweep to the larger cluster tier."
    assert action == "blocked"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    from argus.core.autonomy import read_operator_blocks

    assert read_operator_blocks(supervisor._project_state_root())[-1]["operator_need"] == "spending"


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


# --- interactive escalation stays at dev's rate ------------------------------
_EVERYDAY_QUESTIONS = (
    "pandas or polars?",
    "reduce dataset size?",
    "which spec line wins?",
)


@pytest.mark.parametrize("question", _EVERYDAY_QUESTIONS)
@pytest.mark.parametrize("mode", ["pragmatic", "autonomous"])
def test_everyday_unclassified_questions_never_reach_the_operator(
    monkeypatch, question, mode
) -> None:
    """dev escalated none of these (no keyword matched); neither may we."""
    from argus.core.autonomy import assess_operator_intervention
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    # A Reviewer or runner question nobody classified.
    assert assess_operator_intervention(question=question, mode=mode).required is False
    # A Reviewer request the Reviewer itself marked as the team's.
    assert assess_operator_intervention(
        question=question, operator_need="none", mode=mode
    ).required is False
    # A technical replan carrying the question.
    assert adjudicate_plan_challenge(
        {"authority_impact": "technical", "challenge": question},
        reviewer_status="replan_requested",
        operator_question=question,
    ).action != "ask_operator"


@pytest.mark.parametrize("question", _EVERYDAY_QUESTIONS)
def test_everyday_engineer_questions_without_explicit_handoff_reach_the_reviewer(
    question,
) -> None:
    from argus.core.autonomy import assess_operator_intervention
    from argus.core.role_handoff import parse_engineer_handoff

    handoff = parse_engineer_handoff(
        f"Decision:\nMILESTONE_STATUS=continue\nOPERATOR_QUESTION={question}\n"
    )
    assert handoff.source != "structured"
    assert assess_operator_intervention(
        question=handoff.operator_question,
        operator_need=handoff.operator_need,
        mode="pragmatic",
        unclassified_requires_operator=handoff.source == "structured",
    ).required is False


@pytest.mark.parametrize("question", _EVERYDAY_QUESTIONS)
def test_reviewer_none_in_pragmatic_mode_continues_instead_of_parking(
    tmp_path, monkeypatch, question
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_AUTONOMY_MODE", "pragmatic")

    class _Runner:
        def execute(self, **_kwargs: Any) -> _Outcome:
            outcome = _Outcome(operator_question=question)
            outcome.final_review_reason = "A choice is open."
            outcome.final_review_next_action = ""
            outcome.final_planner_report = {"operator_need": "none"}
            return outcome

    supervisor, sink = _supervisor(tmp_path, runner=_Runner())
    item = supervisor.memory.backlog.add(BacklogItem.new(title="Load data", objective="load"))
    supervisor.tick()
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status != "paused_operator"
    assert not any(
        event["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING for event in sink.events
    )


# --- --no-operator lasts one run ---------------------------------------------
def test_no_operator_flag_is_not_persisted_with_the_project(tmp_path, monkeypatch) -> None:
    import argus.core.autonomy as autonomy

    assert not hasattr(autonomy, "persist_operator_availability")
    assert not hasattr(autonomy, "adopt_persisted_operator_availability")


def test_operator_availability_is_a_visible_cockpit_switch() -> None:
    from argus.core.knobs import cockpit_editable_names, normalize_cockpit_knob_value

    assert "ARGUS_SKILL_OPERATOR_AVAILABLE" in cockpit_editable_names()
    assert normalize_cockpit_knob_value("ARGUS_SKILL_OPERATOR_AVAILABLE", "off") == "0"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_OPERATOR_AVAILABLE", "on") == "1"


def test_saved_knob_is_honoured_not_only_the_environment(monkeypatch) -> None:
    from argus.core.autonomy import operator_available

    monkeypatch.delenv("ARGUS_SKILL_OPERATOR_AVAILABLE", raising=False)
    monkeypatch.setattr(
        "argus.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_OPERATOR_AVAILABLE": "0"},
    )
    assert operator_available() is False


# --- assumptions: per run, the chosen reading, blocks are not assumptions ----
def test_assumptions_are_scoped_to_the_run(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import (
        read_autonomous_assumptions,
        record_autonomous_assumption,
        start_autonomy_run,
    )

    start_autonomy_run()
    record_autonomous_assumption(tmp_path, item_id="", conflict="old conflict", source="x")
    assert read_autonomous_assumptions(tmp_path)
    start_autonomy_run()
    assert read_autonomous_assumptions(tmp_path) == []


def test_round_level_forbid_records_the_question_and_blocks_credentials(
    tmp_path, monkeypatch
) -> None:
    from argus.core.autonomy import (
        read_autonomous_assumptions,
        read_operator_blocks,
        start_autonomy_run,
    )
    from argus.core.models import ReviewDecision
    from argus.engineer.round_settlement import _enforce_operator_question_policy

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    start_autonomy_run()
    config = SimpleNamespace(operator_question_policy_root=tmp_path, operator_questions_allowed=True)
    scope = ReviewDecision(
        status="blocked", reason="r", next_action="",
        operator_question="Which spec line wins?",
        planner_report={"operator_need": "scope_or_authority"},
    )
    _enforce_operator_question_policy(scope, supervised_config=config, state=SimpleNamespace(rounds=[]))
    assert read_autonomous_assumptions(tmp_path)[-1]["conflict"] == "Which spec line wins?"
    cred = ReviewDecision(
        status="blocked", reason="r", next_action="",
        operator_question="Provide the production key?",
        planner_report={"operator_need": "credentials"},
    )
    _enforce_operator_question_policy(cred, supervised_config=config, state=SimpleNamespace(rounds=[]))
    assert len(read_autonomous_assumptions(tmp_path)) == 1
    assert read_operator_blocks(tmp_path)[-1]["operator_need"] == "credentials"


def test_engineer_states_the_reading_it_chose(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import read_autonomous_assumptions, start_autonomy_run
    from argus.engineer.round_self_review import _record_stated_assumption

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    start_autonomy_run()
    outcome = SimpleNamespace(
        decision=None,
        engineer_message=(
            "Done.\nDecision:\nMILESTONE_STATUS=done\n"
            "ASSUMPTION=The release rule wins over the WIP rule for MB-1010\n"
            "NEXT_OWNER=reviewer"
        ),
    )
    _record_stated_assumption(
        outcome, SimpleNamespace(operator_question_policy_root=tmp_path)
    )
    rows = read_autonomous_assumptions(tmp_path)
    assert rows[-1]["reading"] == "The release rule wins over the WIP rule for MB-1010"
    from argus.core.autonomy import render_autonomous_assumptions

    assert "assumed: The release rule wins" in render_autonomous_assumptions(tmp_path)


def test_credential_block_is_not_settled_as_an_assumption(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import (
        read_autonomous_assumptions,
        record_operator_block,
        start_autonomy_run,
    )
    from argus.planner import PlannerVerdict, WaitingContract

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    start_autonomy_run()
    supervisor, _sink = _supervisor(tmp_path, runner=object())
    record_operator_block(
        supervisor._project_state_root(), item_id="i", reason="needs the key",
        operator_need="credentials",
    )
    verdict = PlannerVerdict(
        project_done=False,
        reason="wait for the key",
        waiting=True,
        waiting_reason="wait for the key",
        waiting_contract=WaitingContract(
            blocker_fingerprint="key",
            recheck_condition="the operator provides the key",
            recheck_token="key",
            wait_mode="event",
            wake_on=("authorization",),
            operator_action_required=True,
        ),
    )
    from argus.life.supervisor._constants import PLAN_RETRY

    assert supervisor._record_planner_waiting(verdict) != PLAN_RETRY
    assert read_autonomous_assumptions(supervisor._project_state_root()) == []


def test_completion_report_prompt_names_each_assumption() -> None:
    from argus.roles.prompts.manager import build_project_completion_report_prompt

    prompt = build_project_completion_report_prompt(
        objective="o",
        completion_reason="done",
        completion_context={"autonomous_assumptions": [{"conflict": "c", "reading": "r"}]},
    )
    assert "Name each recorded assumption" in prompt
    assert "Name each recorded assumption" not in build_project_completion_report_prompt(
        objective="o", completion_reason="done", completion_context={}
    )


def test_task_completed_message_lists_the_assumptions(tmp_path, monkeypatch) -> None:
    import json as _json

    from argus.core.autonomy import record_autonomous_assumption, start_autonomy_run
    from argus.life.event_log import JsonlEventSink

    start_autonomy_run()
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    record_autonomous_assumption(
        supervisor._project_state_root(), item_id="", reading="Hello goes to stdout",
        source="engineer",
    )
    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-1",
        "title": "Write greet.py",
        "success": True,
        "status": "done",
        "summary": "Wrote greet.py.",
        "overall_complete": True,
        "outcome": {"review_status": "done"},
    })
    texts = [
        _json.loads(line)["text"]
        for line in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any("assumed: Hello goes to stdout" in text for text in texts)


# --- restarts after the wait exit ---------------------------------------------
def test_web_start_resumes_the_campaign_a_wait_exit_left(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import OPERATOR_WAIT_EXIT_FILENAME
    from argus.webapi import daemon_lifecycle

    life_dir = tmp_path / "projects" / "s-1"
    life_dir.mkdir(parents=True)
    (life_dir / OPERATOR_WAIT_EXIT_FILENAME).write_text("{}", encoding="utf-8")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(daemon_lifecycle, "project_life_dir", lambda sid, **_k: life_dir)
    monkeypatch.setattr(
        daemon_lifecycle.daemon_worker,
        "read_daemon_status",
        lambda _d: SimpleNamespace(alive=False),
    )
    monkeypatch.setattr(
        daemon_lifecycle,
        "_worker_config_from_env",
        lambda d, r: SimpleNamespace(continuous_open_ended=True, continuous_objective=""),
    )

    def stop_here(*_a, **_k):
        raise RuntimeError("stop")

    def record(life):
        seen["read"] = True
        return SimpleNamespace(enabled=True, objective="o", open_ended=False, done_reason="")

    monkeypatch.setattr(daemon_lifecycle.daemon_worker, "read_continuous_state", record)
    monkeypatch.setattr(daemon_lifecycle, "_max_active_daemons", stop_here)
    with pytest.raises(RuntimeError):
        daemon_lifecycle.start_project_daemon("s-1", global_root=tmp_path)
    # The marker made the start adopt the bounded campaign it left behind.
    assert seen.get("read") is True


def test_framework_deployment_answer_also_restarts(tmp_path, monkeypatch) -> None:
    import inspect

    from argus.apps.cli import _core

    source = inspect.getsource(_core._cmd_answer)
    deployment = source.split('if card.get("decision_kind") == "framework_deployment":', 1)[1]
    deployment = deployment.split("blocked, continuation =", 1)[0]
    assert "_restart_worker_after_operator_wait_exit(bundle)" in deployment


# --- item 6: a run blocked on an operator-only need ends -----------------------
def test_operator_only_block_with_nothing_runnable_ends_after_one_more_pass(
    tmp_path, monkeypatch
) -> None:
    from argus.core.autonomy import record_operator_block, start_autonomy_run
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    start_autonomy_run()
    supervisor, sink = _supervisor(tmp_path, runner=object())
    record_operator_block(
        supervisor._project_state_root(), item_id="i", reason="needs the staging key",
        operator_need="credentials",
    )
    assert supervisor._pending_operator_questions() is None
    assert supervisor._operator_only_blocks()
    worker = LifeWorkerRunMixin()
    # First pass after the block: the Planner gets its chance.
    assert worker._bounded_operator_wait_expired([supervisor]) is False
    assert worker._bounded_operator_wait_expired([supervisor]) is True
    blocks = [e for e in sink.events if e["type"] == EventType.LIFE_LIFECYCLE_BLOCK]
    assert blocks and blocks[-1]["text"].startswith("blocked: needs operator")
    # Runnable work resets it.
    supervisor.memory.backlog.add(BacklogItem.new(title="Other", objective="other"))
    assert supervisor._operator_only_blocks() is None
