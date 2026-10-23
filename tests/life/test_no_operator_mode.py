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

from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from tests.role_rules import rule


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


class _QuestionRunner:
    def execute(self, **_kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            operator_question="Which of the two conflicting release rules wins?",
        )
        outcome.final_review_reason = "Two requirements conflict."
        outcome.final_review_next_action = ""
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


def test_no_operator_mission_question_parks_for_the_manager(
    tmp_path, monkeypatch
) -> None:
    """A role handed the work to the operator; with none, the Manager decides the card."""
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    supervisor, _sink = _supervisor(tmp_path, runner=_QuestionRunner())
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
    supervisor, _sink = _supervisor(tmp_path, runner=_QuestionRunner())
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
        reviewer_status="replan_requested", review_reason="c", next_action=alternative,
    )
    assert decision.action == "replace"


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
        summary="the operator decides which release rule wins",
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
    from argus.roles.prompts.engineer import engineer_handoff_rule

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert engineer_handoff_rule().endswith("the Manager decides in their place.")
    rules = rule("engineer", "handing-off/SKILL.md")
    assert "write it in `assumptions`" in rules
    assert "goes `to=manager`." in rules
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert "No operator" not in engineer_handoff_rule()


def test_bounded_completion_summary_names_the_assumptions(tmp_path) -> None:
    from argus.core.autonomy import record_autonomous_assumption, render_autonomous_assumptions

    # A conflict with no reading chosen decided nothing.
    record_autonomous_assumption(tmp_path, item_id="x", conflict="Two release rules conflict.", source="x")
    assert render_autonomous_assumptions(tmp_path) == ""
    record_autonomous_assumption(
        tmp_path, item_id="x", conflict="Two release rules conflict.", reading="The stricter one.",
        source="engineer",
    )
    text = render_autonomous_assumptions(tmp_path)
    assert text.startswith("Decided without asking you")
    assert "I went with: The stricter one. (rather than Two release rules conflict.)" in text
    assert render_autonomous_assumptions(tmp_path, chinese=True).startswith("这些是没问你就定下的")


def test_a_manager_answer_is_named_the_same_way_everywhere(tmp_path) -> None:
    from argus.core.autonomy import record_autonomous_assumption, render_autonomous_assumptions

    record_autonomous_assumption(
        tmp_path, item_id="x", conflict="Which rule wins?", reading="The newer one.",
        source="manager_decision",
    )
    assert "the Manager decided for you: The newer one." in render_autonomous_assumptions(tmp_path)
    assert "经理替你定了：The newer one." in render_autonomous_assumptions(tmp_path, chinese=True)


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
    record_autonomous_assumption(tmp_path, item_id="", reading="old reading", source="x")
    assert read_autonomous_assumptions(tmp_path)
    start_autonomy_run()
    assert read_autonomous_assumptions(tmp_path) == []




def test_task_completed_message_lists_the_assumptions(tmp_path, monkeypatch) -> None:

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
        supervisor._project_state_root(), item_id="task-1", reading="Hello goes to stdout",
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
    from tests.manager_inbox import team_reports

    texts = [report["text"] for report in team_reports(tmp_path)]
    assert any("I went with: Hello goes to stdout" in text for text in texts)


def test_assumption_wording_never_targets_graded_deliverables(monkeypatch, tmp_path) -> None:
    from argus.core.operator_context import build_operator_context_block

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    (tmp_path / "life").mkdir()
    block, _ = build_operator_context_block("engineer", tmp_path / "life")
    assert "in its handoff's assumptions" in block
    assert "in the deliverable" not in block


def test_engineer_evidence_for_the_next_holder_lives_in_the_working_directory() -> None:
    # dogfood8 sizefmt: build logs under copilot-home/session-state/<id>/files
    # were named in the note, and the sandboxed Reviewer could not read them.
    from argus.core.workspace_facts import IGNORED_NAMES
    from argus.roles.prompts.engineer import engineer_handoff_rule

    assert "`.argus/evidence/`" in engineer_handoff_rule()
    assert ".argus" in IGNORED_NAMES, "evidence is not mistaken for the deliverable"


def test_the_manager_sees_each_recorded_assumption_once_before_deciding(tmp_path) -> None:
    # dogfood8 sizefmt: four supervisions restated one reading in new words, and
    # the operator read it four times in the result and the report.
    from argus.core.autonomy import record_autonomous_assumption
    from argus.daemon.state import write_continuous_config
    from argus.manager.observation import observe_project
    from argus.manager.supervision import _prompt

    reading = "format_mib 沿用 format_kib 的规则，非负字节数除以 1024**2 后丢弃小数；1572864 输出 \"1 MiB\"。"
    write_continuous_config(tmp_path, enabled=True, objective="Add format_mib to sizefmt.")
    record_autonomous_assumption(
        tmp_path, item_id="e102a530003d", source="supervise", reading=reading,
        conflict="沿用 format_gib 的最近整数、半数进位规则；1572864 输出 \"2 MiB\"。",
    )
    observation = observe_project(tmp_path)
    assert [row["reading"] for row in observation.facts["recorded_assumptions"]] == [reading]
    prompt = _prompt(observation)
    assert "非负字节数除以 1024**2 后丢弃小数" in prompt
    assert "only a reading this decision chooses for the first time" in prompt
