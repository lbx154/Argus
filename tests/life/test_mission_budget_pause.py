"""A reached per-mission budget becomes an operator decision, never a silent stop."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus.adapters.memory_backend import MemoryBackend
from argus.core.budget_signal import (
    MissionBudget,
    mission_usage_summary,
    record_mission_budget_pause,
    take_mission_budget_pause,
)
from argus.core.event_catalog import EventType
from argus.core.usage import UsageLedger, UsageRecord
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.memory import BacklogItem, LifeMemory, MemoryBundle
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._mission_budget_pause import mission_budget_card
from argus.manager import front_door
from argus.reviewer import Reviewer, ReviewerConfig
from argus.webapi import manager_pending_question


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1


def _ledger_root(memory: LifeMemory) -> Path:
    return Path(getattr(memory, "project_root", None) or memory.root)


def _spend(root: Path, usage_mission_id: str, count: int) -> None:
    now = time.time()
    UsageLedger(root, migrate_legacy=False).append_many([
        UsageRecord(
            call_id=f"{usage_mission_id}-{index}", project_id="p", mission_id=usage_mission_id,
            provider="copilot", model="m", run_label="engineer", started_at=now - 1,
            completed_at=now, status="succeeded", input_tokens=1, cached_input_tokens=0,
            output_tokens=1, reasoning_output_tokens=0, premium_requests=1.0,
            pricing_status="priced", pricing_tier="t", cost_usd=0.04, cost_basis="provider",
        )
        for index in range(count)
    ])


class _RoundLoopRunner:
    """Runs the real round loop; the only backend is one that must not be called."""

    def __init__(self, memory: LifeMemory, *, prior_spend: int) -> None:
        self.memory = memory
        self.prior_spend = prior_spend
        self.backend = MemoryBackend()

    def execute(self, **kwargs: Any) -> _Outcome:
        attempt = str(kwargs["usage_mission_id"])
        item_id = attempt.split(":attempt:", 1)[0]
        root = _ledger_root(self.memory)
        _spend(root, attempt, self.prior_spend)
        engineer = SupervisedEngineer(
            engineer_runner=self.backend,
            reviewer=Reviewer(runner=self.backend),
            engineer_config=EngineerConfig(model="m"),
            reviewer_config=ReviewerConfig(model="m"),
        )
        status, rounds, _final, reason, _tid = engineer.run(
            objective="keep going",
            engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
            supervised_config=SupervisedConfig(
                max_rounds=3,
                session_id=item_id,
                operator_question_policy_root=root,
                engineer_log_path=str(root / "events.jsonl"),
            ),
            workdir=root,
            on_event=lambda _event: None,
        )
        return _Outcome(success=False, status=status, stop_reason=reason, rounds=len(rounds))


class _ManagerWaitRunner:
    def execute(self, **_kwargs: Any) -> _Outcome:
        return _Outcome(success=False, status="paused_operator", stop_reason="Manager asked to wait")


def _supervisor(memory: LifeMemory, runner: Any, sink: _Sink) -> LifeSupervisor:
    return LifeSupervisor(
        memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=3), poll_interval_seconds=0.01),
    )


@pytest.fixture(autouse=True)
def _budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "3")
    monkeypatch.delenv("ARGUS_SKILL_MISSION_BUDGET_USD", raising=False)


def _item(memory: LifeMemory, title: str = "long task", objective: str = "keep going") -> BacklogItem:
    return memory.backlog.add(BacklogItem.new(
        title=title, objective=objective,
        manager_decision={"routed": True, "vertical": "software"},
    ))


def test_round_loop_budget_stop_reaches_the_operator_end_to_end(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    item = _item(memory)
    runner = _RoundLoopRunner(memory, prior_spend=3)

    result = _supervisor(memory, runner, sink).tick()

    assert runner.backend.history == []  # stopped before any further paid call
    assert result is not None and result["status"] == "paused_operator"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert "per-mission budget" in stored.pending_question
    card = stored.operator_decision
    assert card["decision_kind"] == "mission_budget"
    assert [option["id"] for option in card["options"]] == ["resume", "stop"]
    assert card["spend"]["premium_requests"] == 3
    asked = [event for event in sink.events if event.get("type") == EventType.LIFE_OPERATOR_QUESTION_PENDING]
    assert asked and asked[-1]["item_id"] == item.id
    assert take_mission_budget_pause(_ledger_root(memory), item.id) is None


def test_under_budget_round_loop_proceeds_to_the_engineer(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    _item(memory)
    runner = _RoundLoopRunner(memory, prior_spend=2)
    _supervisor(memory, runner, _Sink()).tick()
    assert runner.backend.history  # the Engineer was called


def test_missing_marker_still_parks_by_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def unwritable(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr("argus.core.budget_signal.record_mission_budget_pause", unwritable)
    memory = LifeMemory.open(tmp_path / "life")
    item = _item(memory)
    _supervisor(memory, _RoundLoopRunner(memory, prior_spend=4), _Sink()).tick()
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.operator_decision["decision_kind"] == "mission_budget"
    assert stored.operator_decision["spend"]["premium_requests"] == 4


def test_stale_marker_never_replaces_a_manager_wait(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = _item(memory)
    root = _ledger_root(memory)
    record_mission_budget_pause(
        root, item.id, reached="old", summary=mission_usage_summary(root, item.id),
        budget=MissionBudget(requests=1),
    )
    _supervisor(memory, _ManagerWaitRunner(), _Sink()).tick()
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.operator_decision.get("decision_kind") != "mission_budget"
    assert take_mission_budget_pause(root, item.id) is None  # cleared


def test_chinese_task_gets_a_chinese_question(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = _item(memory, title="整理实验结果", objective="继续推进")
    _supervisor(memory, _RoundLoopRunner(memory, prior_spend=3), _Sink()).tick()
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert "单任务预算" in stored.pending_question
    assert [option["label"] for option in stored.operator_decision["options"]] == ["继续", "停止"]


def _budget_card_project(tmp_path: Path, sid: str = "s-budget") -> tuple[Any, dict[str, Any]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mem = MemoryBundle.for_cwd(workspace, global_root=tmp_path, fingerprint=sid)
    mem.init()
    write_continuous_config(mem.project_root, enabled=True, objective="standing work")
    item = mem.backlog.add(BacklogItem.new(title="Long task", objective="Do work", item_id="item"))
    question, card = mission_budget_card(
        item,
        {"reached": "3 of 3 premium requests", "budget": {"requests": 3, "usd": 0},
         "spent": {"calls": 3, "premium_requests": 3}},
        project_id=sid,
    )
    mem.backlog.update(item.id, status="paused_operator", pending_question=question, operator_decision=card)
    return mem, card


@pytest.fixture()
def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        front_door, "manager_triage",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("decisions must not need a model call")),
    )


def test_stop_on_a_budget_card_stops_without_queueing_work(tmp_path: Path, _no_model: None) -> None:
    mem, card = _budget_card_project(tmp_path)
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-budget", card["id"], "stop", global_root=tmp_path,
    )
    assert result is not None and result["stopped"] is True
    rows = mem.backlog.all()
    assert [row.id for row in rows] == ["item"]  # no continuation was queued
    assert rows[0].status == "aborted"
    assert read_continuous_state(mem.project_root).enabled is False


def test_continue_on_a_budget_card_queues_one_continuation(tmp_path: Path, _no_model: None) -> None:
    mem, card = _budget_card_project(tmp_path)
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-budget", card["id"], "resume", global_root=tmp_path,
    )
    assert result is not None and result["application_status"] == "accepted"
    assert result["resume_requested"] is True
    continuation = [row for row in mem.backlog.all() if row.id != "item"]
    assert len(continuation) == 1 and continuation[0].status == "pending"
