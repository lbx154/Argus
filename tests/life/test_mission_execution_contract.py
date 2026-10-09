"""Observable settlement boundaries of one claimed, metered mission attempt."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from argus.apps._runtime_backends import _Outcome
from argus.core.event_catalog import EventType
from argus.core.usage import UsageLedger
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._mission_execution_helpers import _MissionRunState
from argus.skills.vertical_select import persist_vertical


class _ObservingSink:
    def __init__(self, memory: LifeMemory) -> None:
        self.memory = memory
        self.completions: list[tuple[dict[str, Any], str]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == EventType.LIFE_MISSION_COMPLETED:
            stored = next(row for row in self.memory.backlog.all() if row.id == event["item_id"])
            self.completions.append((dict(event), stored.status))


class _MeteredRunner:
    def __init__(self, outcome: _Outcome | Exception) -> None:
        self.outcome = outcome
        self.calls = 0
        self.on_execute: Callable[[], None] = lambda: None

    def execute(self, *, sink: Any, **_kwargs: Any) -> _Outcome:
        self.calls += 1
        sink.handle_event({
            "type": EventType.ROUND_MAIN_COMPLETED,
            "input_tokens": 1_000,
            "output_tokens": 100,
        })
        self.on_execute()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _mission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome, *, tags=()):
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software")
    memory = LifeMemory.open(tmp_path / "life")
    runner = _MeteredRunner(outcome)
    sink = _ObservingSink(memory)
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="Deliver the requested artifact", objective="Complete the requested change.",
        manager_decision={"routed": True, "vertical": "software"},
        tags=list(tags), iterate=False,
    ))
    learning_statuses = []

    def observe_learning(**_kwargs):
        learning_statuses.append(next(row.status for row in memory.backlog.all() if row.id == item.id))

    monkeypatch.setattr(supervisor, "_evolve_runtime_skills_after_mission", observe_learning)
    return supervisor, memory, item, runner, sink, learning_statuses


def test_state_rejects_undeclared_dependencies_without_sharing_mutable_defaults():
    item = BacklogItem.new(title="same task", objective="complete it")
    first, second = _MissionRunState(item), _MissionRunState(item)
    # State is an attempt identity, not structural equality over live services.
    assert first != second
    first.item_tags.add("planner")
    first.plan_revision_witness["plan_id"] = "first-plan"
    first.stage_transition["action"] = "hold"
    assert second.item_tags == set()
    assert second.plan_revision_witness == {}
    assert second.stage_transition == {}
    with pytest.raises(AttributeError):
        first.undeclared_stage_owner = "manager"


@pytest.mark.parametrize(
    "outcome,tags,result_status,stored_status,publishes",
    [
        (_Outcome(True, "done"), (), "done", "done", True),
        (RuntimeError("execution failed"), (), "error", "failed", True),
        # A recoverable stop takes precedence over the stale stage HOLD.
        (_Outcome(False, "budget_exhausted", stage_transition={"action": "hold"}),
         (), "paused_budget", "paused_budget", True),
        (_Outcome(True, "done", stage_transition={"action": "advance"}),
         (), "stage_continues", "pending", True),
        (_Outcome(True, "done", stage_transition={"action": "hold"}),
         (), "stage_hold", "failed", True),
        (_Outcome(False, "research_incomplete", stage_transition={"action": "advance"}),
         ("planner", "scope:bounded"), "done", "done", True),
    ],
    ids=["done", "runner_error", "pause_before_hold", "next_stage", "stage_hold", "bounded_node"],
)
def test_execution_branches_preserve_metering_and_publication_order(
    tmp_path, monkeypatch, outcome, tags, result_status, stored_status, publishes,
):
    supervisor, memory, item, runner, sink, learning = _mission(
        tmp_path, monkeypatch, outcome, tags=tags,
    )

    result = supervisor._run_one(item)

    assert result["status"] == result_status
    assert runner.calls == 1
    # Reload the persisted row and call ledger, rather than inspecting scratch state.
    assert next(row.status for row in memory.backlog.all() if row.id == item.id) == stored_status
    records = UsageLedger(memory.root, migrate_legacy=False).records()
    assert len(records) == 1
    assert records[0].mission_id == f"{item.id}:attempt:1"
    assert records[0].input_tokens == 1_000
    assert result["known_cost_usd"] == records[0].cost_usd
    assert len(sink.completions) == int(publishes)
    if publishes:
        event, status_at_publish = sink.completions[0]
        assert status_at_publish == stored_status
        assert event["status"] == result_status
        assert event["known_cost_usd"] == result["known_cost_usd"]
    assert learning == ([stored_status] if publishes and result_status != "paused_budget" else [])


def test_intentional_hold_continues_and_publishes_before_next_attempt(tmp_path, monkeypatch):
    from argus.core.transcript import append_turn, read_turns

    outcome = _Outcome(True, "done", stage_transition={
        "action": "hold", "diagnostic": "intentional_hold", "target_stage": "ingest",
        "reason": "继续研究长程信用分配，保留已核实的知识。",
    })
    supervisor, memory, item, runner, sink, _ = _mission(tmp_path, monkeypatch, outcome)
    append_turn(memory.root, "operator", "学习智能体强化学习")
    first = supervisor._run_one(item)
    assert first["status"] == "stage_continues"
    event, stored_status = sink.completions[-1]
    assert stored_status == "pending"
    assert event["overall_complete"] is False
    assert event["campaign_continues"] is True
    assert event["outcome"]["execution_status"] == "incomplete"
    supervisor._publish_mission_completion_message(event)
    assert "自动开始下一轮" in read_turns(memory.root)[-1]["text"]
    assert "长程信用分配" in read_turns(memory.root)[-1]["text"]
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert "长程信用分配" in supervisor._build_mission_prelude(stored)
    from argus.core.mission_view import update_mission_view_event
    view = update_mission_view_event(memory.root, event)
    assert view["mission"]["status"] == "continued"

    runner.outcome = _Outcome(True, "done", stage_transition={"action": "complete"})
    second = supervisor._run_one(next(row for row in memory.backlog.all() if row.id == item.id))
    assert second["status"] == "done"
    assert runner.calls == 2
    assert len(sink.completions) == 2
    assert len(UsageLedger(memory.root, migrate_legacy=False).records()) == 2


def test_repeated_hold_is_bounded_across_supervisor_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD", "2")
    outcome = _Outcome(True, "done", stage_transition={
        "action": "hold", "diagnostic": "intentional_hold", "reason": "Need broader evidence",
    })
    supervisor, memory, item, runner, sink, _ = _mission(tmp_path, monkeypatch, outcome)
    assert supervisor._run_one(item)["status"] == "stage_continues"
    restarted = LifeSupervisor(memory=LifeMemory.open(memory.root), runner=runner,
                               sink=sink, config=supervisor.config)
    second = restarted._run_one(next(row for row in memory.backlog.all() if row.id == item.id))
    assert second["status"] == "no_progress"
    assert "2 attempts" in second["stop_reason"]
    assert sink.completions[-1][1] == "failed"
    assert sink.completions[-1][0]["resumable"] is False


def test_hold_with_operator_question_waits_instead_of_repeating(tmp_path, monkeypatch):
    outcome = _Outcome(True, "done", stage_transition={
        "action": "hold", "diagnostic": "intentional_hold", "reason": "Need authorization",
    })
    outcome.operator_question = "May I publish the private dataset?"
    supervisor, memory, item, runner, sink, _ = _mission(tmp_path, monkeypatch, outcome)
    supervisor._run_one(item)
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.pending_question == outcome.operator_question
    assert len(sink.completions) == 1


def test_measured_progress_allows_multiple_passes_within_one_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD", "2")
    outcome = _Outcome(True, "done", stage_transition={
        "action": "hold", "diagnostic": "intentional_hold", "reason": "Continue extending coverage",
    })
    outcome.final_planner_report = {"forward_progress": True}
    supervisor, memory, item, _, _, _ = _mission(tmp_path, monkeypatch, outcome)
    for _ in range(3):
        stored = next(row for row in memory.backlog.all() if row.id == item.id)
        assert supervisor._run_one(stored)["status"] == "stage_continues"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.consecutive_replans == 0


def test_superseded_outcome_is_metered_without_settling_replacement(tmp_path, monkeypatch):
    outcome = _Outcome(True, "done")
    outcome.acceptance_assessment_superseded = True
    supervisor, memory, item, runner, sink, learning = _mission(tmp_path, monkeypatch, outcome)

    def replace_contract():
        memory.backlog.update(
            item.id, status="pending", objective="New accepted contract.",
            acceptance_check="Use the replacement acceptance check.",
        )

    runner.on_execute = replace_contract

    result = supervisor._run_one(item)

    assert result["status"] == "claim_lost"
    assert result["recoverable"] is True
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert (stored.status, stored.objective, stored.acceptance_check) == (
        "pending", "New accepted contract.", "Use the replacement acceptance check.",
    )
    records = UsageLedger(memory.root, migrate_legacy=False).records()
    assert len(records) == 1 and records[0].input_tokens == 1_000
    assert result["known_cost_usd"] == records[0].cost_usd
    assert sink.completions == []
    assert learning == []
