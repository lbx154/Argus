"""Runs with and without an operator settle operator decisions differently.

With an operator, a question a role hands to the operator is asked. Without
one, nobody is asked and the Manager holds the operator's authority: the
question parks as a card the Manager decides in their place, and a Manager
settling a scope conflict on the most defensible reading records the
assumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    assert stored.status == "parked"
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
    assert stored.status != "parked"
    assert not stored.pending_question
    assert not any(
        event["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING
        for event in sink.events
    )
    challenge = outcome["plan_challenge"]
    assert challenge["autonomous_assumption"] is True
    assert "The operator cannot be asked" in challenge["manager_reason"]
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
def test_no_operator_parks_an_action_for_the_manager_to_decide(
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

    assert action == "ask_operator"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "parked"
    assert stored.operator_decision["status"] == "pending"


def test_no_operator_mission_question_parks_for_the_manager(
    tmp_path, monkeypatch
) -> None:
    """A role handed the work to the operator; with none, the Manager decides the card."""
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, _sink = _supervisor(
        tmp_path, runner=_ClassifiedQuestionRunner("scope_or_authority")
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "blocked"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "parked"
    assert stored.pending_question == "Which of the two conflicting release rules wins?"


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
    assert stored.status == "parked"
    assert stored.pending_question == "Which of the two conflicting release rules wins?"


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


def test_retired_operator_wait_exit_is_accepted_and_ignored(tmp_path) -> None:
    from argus.apps.cli import build_parser
    from argus.daemon.config import LifeWorkerConfig, config_from_payload, config_payload
    from argus.daemon.process import _windows_daemon_command

    # Old launch scripts still parse; nothing reads the value.
    build_parser().parse_args(["--daemon-fg", "--bounded", "--operator-wait-exit", "on"])
    bounded = LifeWorkerConfig(life_dir=tmp_path / "projects" / "s-1", continuous_open_ended=False)
    assert "--operator-wait-exit" not in _windows_daemon_command(bounded)
    payload = {**config_payload(bounded), "operator_wait_exit": True}
    assert not hasattr(config_from_payload(payload), "operator_wait_exit")


@pytest.mark.parametrize(
    "alternative",
    [
        "Load the data with polars instead of pandas.",
        "Reduce the dataset size to the first 10k rows.",
        "Follow the spec line in section 2.",
    ],
)
def test_plan_alternatives_route_exactly_as_on_dev_with_an_operator(
    monkeypatch, alternative
) -> None:
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    decision = adjudicate_plan_challenge(
        {"authority_impact": "technical", "challenge": "c", "alternative": alternative},
        reviewer_status="replan_requested",
    )
    assert decision.action == "replace"


def test_no_operator_irreversible_alternative_parks_for_the_manager(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, _sink = _supervisor(tmp_path, runner=object())
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Ship the fix", objective="ship the fix")
    )
    outcome = _conflict_outcome(
        item.id,
        authority_impact="operator",
        operator_need="irreversible_or_external",
        alternative="Force-push the protected release branch.",
    )
    assert supervisor._adjudicate_mission_challenge(outcome) == "ask_operator"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "parked"


class _ProxyManager:
    """A Manager that answers every card it is asked to decide with ``fields``."""

    def __init__(self, **fields: str) -> None:
        self.fields = fields
        self.prompts: list[str] = []

    def run_turn(self, prompt: str, **_kwargs: Any) -> str:
        self.prompts.append(prompt)
        return "\n".join(f"{key}: {value}" for key, value in self.fields.items())


def _planner_question():
    from argus.planner import PlannerVerdict

    return PlannerVerdict(
        project_done=False,
        reason="the operator decides which release rule wins",
        handoff="operator",
        operator_question="Which release rule wins?",
    )


def _proxy_supervisor(tmp_path, manager: _ProxyManager) -> LifeSupervisor:
    from argus.apps._runtime_construction import _pending_question_resolver_for

    # The web answer path a Manager's choice takes reads a registered project.
    life = tmp_path / "projects" / "s-proxy"
    life.mkdir(parents=True)
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(life),
        runner=object(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
            poll_interval_seconds=0.01,
            pending_question_resolver=_pending_question_resolver_for(life),
        ),
    )
    supervisor._bound_manager = lambda: manager
    return supervisor


def _card(supervisor: LifeSupervisor):
    [card] = [
        row for row in supervisor.memory.backlog.active() if row.status == "parked"
    ]
    return card


@pytest.mark.parametrize("mode", ["no_operator", "forbid"])
def test_manager_decides_a_planner_question_in_the_operators_place(
    tmp_path, monkeypatch, mode
) -> None:
    """The Planner's question goes on a card like any other; with nobody to
    ask, the Manager answers it there."""
    from argus.core.autonomy import read_autonomous_assumptions
    from argus.core.knob_store import write_persisted_knob
    from argus.life.supervisor._constants import PLAN_AWAITING

    if mode == "no_operator":
        monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    else:  # the operator said not to be asked
        write_persisted_knob("ARGUS_SKILL_OPERATOR_AVAILABLE", "0")
    manager = _ProxyManager()
    supervisor = _proxy_supervisor(tmp_path, manager)

    assert supervisor._record_planner_handoff(_planner_question()) == PLAN_AWAITING
    card = _card(supervisor)
    assert card.pending_question == "Which release rule wins?"
    manager.fields = {
        "DECISION_ID": card.operator_decision["id"],
        "OPTION": "custom",
        "NOTE": "The release rule wins.",
        "REASON": "It is the stricter of the two.",
        "EVIDENCE_REFS": "spec.md",
    }

    assert supervisor._decide_parked_questions() is True
    row = read_autonomous_assumptions(supervisor._project_state_root())[-1]
    assert row["source"] == "manager_decision"
    assert row["evidence_refs"] == ["spec.md"]
    assert "The release rule wins." in row["reading"]


def test_a_planner_question_the_manager_names_no_decision_for_stays_open(
    tmp_path, monkeypatch
) -> None:
    from argus.core.autonomy import read_autonomous_assumptions

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor = _proxy_supervisor(tmp_path, _ProxyManager(
        DECISION_ID="decision-other", OPTION="custom", NOTE="x", REASON="y",
    ))

    supervisor._record_planner_handoff(_planner_question())
    assert supervisor._decide_parked_questions() is False
    assert _card(supervisor).pending_question == "Which release rule wins?"
    assert read_autonomous_assumptions(supervisor._project_state_root()) == []


def test_an_operator_only_external_blocker_is_decided_by_the_manager_without_an_operator(
    tmp_path, monkeypatch
) -> None:
    """The host's own question about operator-supplied artifacts has a decider
    too: with nobody to ask, the Manager answers its card."""
    import json

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    manager = _ProxyManager()
    supervisor = _proxy_supervisor(tmp_path, manager)
    project = tmp_path / "project"
    (project / "diagnosis").mkdir(parents=True)
    (project / "diagnosis" / "operator_only_external_blocker.json").write_text(
        json.dumps({
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/heldout.tar"],
        }),
        encoding="utf-8",
    )

    question = supervisor._operator_external_blocker_short_circuit_decision(project_root=project)

    assert question.handoff == "operator"
    supervisor._record_planner_handoff(question)
    manager.fields = {
        "DECISION_ID": _card(supervisor).operator_decision["id"],
        "OPTION": "custom",
        "NOTE": "Evaluate on the public split and say so in the report.",
        "REASON": "The held-out set will not arrive in this run.",
    }
    assert supervisor._decide_parked_questions() is True
    assert len(manager.prompts) == 1


def test_engineer_prompt_hands_only_actions_to_the_manager_without_an_operator(
    monkeypatch,
) -> None:
    from argus.roles.prompts.engineer import engineer_operator_handoff_rule

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    rule = engineer_operator_handoff_rule()
    assert "only for an action only the operator could approve" in rule
    assert rule.endswith("the Manager decides it.")
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    rule = engineer_operator_handoff_rule()
    assert rule.endswith("that parks the task.")


def test_bounded_completion_summary_names_the_assumptions(tmp_path) -> None:
    from argus.core.autonomy import record_autonomous_assumption, render_autonomous_assumptions

    record_autonomous_assumption(
        tmp_path, item_id="x", conflict="Two release rules conflict.", source="mission_challenge"
    )
    text = render_autonomous_assumptions(tmp_path)
    assert text.startswith("Decided without asking the operator")
    assert "Two release rules conflict." in text


def test_a_reviewer_credential_request_parks(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    supervisor, _sink = _supervisor(tmp_path, runner=_ClassifiedQuestionRunner("credentials"))
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="Deploy staging", objective="deploy staging")
    )

    supervisor.tick()

    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "parked"


# --- interactive escalation stays at dev's rate ------------------------------
_EVERYDAY_QUESTIONS = (
    "pandas or polars?",
    "reduce dataset size?",
    "which spec line wins?",
)


@pytest.mark.parametrize("question", _EVERYDAY_QUESTIONS)
def test_everyday_unclassified_questions_never_reach_the_operator(
    monkeypatch, question
) -> None:
    """dev escalated none of these (no keyword matched); neither may we."""
    from argus.core.autonomy import assess_operator_intervention
    from argus.manager.plan_challenge import adjudicate_plan_challenge

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    # A Reviewer or runner question nobody classified.
    assert assess_operator_intervention(question=question).required is False
    # A Reviewer request the Reviewer itself marked as the team's.
    assert assess_operator_intervention(
        question=question, operator_need="none"
    ).required is False
    # A technical replan carrying the question.
    assert adjudicate_plan_challenge(
        {"authority_impact": "technical", "challenge": question},
        reviewer_status="replan_requested",
    ).action != "ask_operator"


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


def test_assumption_wording_never_targets_graded_deliverables(monkeypatch, tmp_path) -> None:
    from argus.core.autonomy import AUTONOMOUS_ASSUMPTION_INSTRUCTION
    from argus.core.operator_context import build_operator_context_block

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    (tmp_path / "life").mkdir()
    block, _ = build_operator_context_block("engineer", tmp_path / "life")
    for text in (AUTONOMOUS_ASSUMPTION_INSTRUCTION, block):
        assert "CHECKPOINT.md and the run report" in text
        assert "in the deliverable" not in text
