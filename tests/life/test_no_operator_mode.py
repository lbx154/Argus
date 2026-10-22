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
    assert rule.endswith("that parks the task until they answer.")


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
    assert "Two release rules conflict. → I went with: The stricter one." in text
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
    assert any("I went with: Hello goes to stdout" in text for text in texts)


def test_assumption_wording_never_targets_graded_deliverables(monkeypatch, tmp_path) -> None:
    from argus.core.operator_context import build_operator_context_block

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    (tmp_path / "life").mkdir()
    block, _ = build_operator_context_block("engineer", tmp_path / "life")
    assert "CHECKPOINT.md and the run report" in block
    assert "in the deliverable" not in block


# --- HO-6: one policy, nothing waits for the operator --------------------------
def test_the_reviewer_prompt_never_says_to_ask_an_operator_who_cannot_be_asked(monkeypatch) -> None:
    from argus.reviewer import Reviewer

    def static() -> str:
        return Reviewer(runner=None, skill_store=None)._build_static_preamble(
            objective="o", operator_messages=[], planner_review_instruction="",
            round_index=1, session_id=None, main_summary="s", main_error=None,
            prior_checkpoint={},
        )

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert "→ operator" in static()
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    text = static()
    assert "→ operator" not in text and "Ask only for operator-owned" not in text
    assert "the Manager decides in their place" in text


def test_every_decision_taken_for_the_operator_is_listed(tmp_path) -> None:
    from argus.core.autonomy import record_autonomous_assumption, render_autonomous_assumptions

    for index in range(12):
        record_autonomous_assumption(
            tmp_path, item_id=f"t{index}", conflict=f"Question {index}?", reading=f"Answer {index}.",
            source="manager_decision",
        )
    lines = render_autonomous_assumptions(tmp_path).splitlines()[1:]
    assert len(lines) == 12 and "Answer 0." in lines[0]


def test_the_manager_can_end_any_card_it_decides_but_not_fix_what_it_cannot(
    tmp_path, monkeypatch,
) -> None:
    """Principle 8's credential exception: what the operator alone can supply,
    the Manager can only work around or end."""
    from argus.core.operator_decision import END_TASK_OPTION, build_operator_decision
    from argus.manager.proxy_decision import decide_for_operator

    card = build_operator_decision(
        item_id="t", title="Publish", reason="r", question="Which token?",
        options=["Use the release token", {"id": END_TASK_OPTION, "label": "an agent's own option"}],
    )
    # Only the host offers the way out; an agent's option never takes its id.
    assert [row["id"] for row in card["options"]] == ["option-1", "option-2"]
    ends = _ProxyManager(DECISION_ID=card["id"], OPTION=END_TASK_OPTION, REASON="No token here.")
    assert decide_for_operator(ends, card, why="w").option == END_TASK_OPTION
    assert f"- {END_TASK_OPTION}: end this task" in ends.prompts[0]

    provider = build_operator_decision(
        item_id="p", title="t", reason="r", question="Fix the model?",
        options=[{"id": "resume", "label": "Resume after fixing the configuration"},
                 {"id": "drop", "label": "Drop this task"}],
    ) | {"decision_kind": "provider_configuration"}
    resumes = _ProxyManager(DECISION_ID=provider["id"], OPTION="resume", REASON="Try again.")
    assert decide_for_operator(resumes, provider, why="w") is None
    assert "- resume:" not in resumes.prompts[0] and "- drop:" in resumes.prompts[0]


def test_the_manager_ends_a_task_through_the_path_an_answer_takes(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import read_autonomous_assumptions
    from argus.core.operator_decision import END_TASK_OPTION

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    manager = _ProxyManager()
    supervisor = _proxy_supervisor(tmp_path, manager)
    supervisor._record_planner_handoff(_planner_question())
    card = _card(supervisor)
    manager.fields = {
        "DECISION_ID": card.operator_decision["id"], "OPTION": END_TASK_OPTION,
        "REASON": "Only the operator holds the release key.",
    }

    assert supervisor._decide_parked_questions() is True
    ended = next(row for row in supervisor.memory.backlog.history() if row.id == card.id)
    assert ended.status == "aborted" and not ended.pending_question
    assert ended.operator_decision["decided_by"] == "manager"
    assert ended.operator_decision["selected_option"] == END_TASK_OPTION
    row = read_autonomous_assumptions(supervisor._project_state_root())[-1]
    assert row["source"] == "manager_decision" and row["reading"].startswith("End this task")


def test_no_operator_wait_is_left_once_the_operator_cannot_be_asked() -> None:
    """Architecture: every place that parks work for an operator answer or
    ends with "operator input required" reads the one switch first, and the
    operator is never told that something waits for them while the Manager
    holds their authority."""
    import re
    from pathlib import Path

    import argus
    from argus.core.mission_view._wording import _SENTENCES

    root = Path(argus.__file__).parent
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"operator_input_required"' in text and "stop_kinds.py" not in path.name:
            assert "operator_questions_can_be_asked" in text, path
    english, chinese = _SENTENCES["proxy_undecided"]
    assert not re.search(r"waits? for your answer", english) and "等你" not in chinese
