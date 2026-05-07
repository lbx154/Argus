"""Tests for ``LifeSupervisor`` and ``LifeBudget``.

Uses a fake mission runner that emits the same shape of events the real
``MissionExecutor`` does, so we can verify cost tracking / journal
entries / budget gating without spinning up codex.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from argus_skill.life.memory import (
    BacklogItem,
    JournalEntry,
    LifeMemory,
)
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    _CostTrackingSink,
    _price_for,
)


# ---------- helpers --------------------------------------------------------

@dataclass
class _FakeOutcome:
    success: bool = True
    status: str = "success"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False


class _FakeRunner:
    """Stand-in for MissionExecutor.

    The supervisor passes a sink; we emit a couple of token-bearing
    events to that sink, then return ``response``.
    """

    def __init__(
        self,
        *,
        engineer_in: int = 1000,
        engineer_out: int = 500,
        reviewer_in: int = 200,
        reviewer_out: int = 100,
        response_factory: Callable[[str, str], _FakeOutcome] | None = None,
        raise_on_objective: str | None = None,
    ) -> None:
        self.engineer_in = engineer_in
        self.engineer_out = engineer_out
        self.reviewer_in = reviewer_in
        self.reviewer_out = reviewer_out
        self.response_factory = response_factory or (
            lambda obj, prelude: _FakeOutcome()
        )
        self.raise_on_objective = raise_on_objective
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _FakeOutcome:
        self.calls.append({
            "objective": objective,
            "prelude_context": prelude_context,
        })
        if self.raise_on_objective and self.raise_on_objective in objective:
            raise RuntimeError("boom")
        # Emit token-bearing events shaped like the real engine.
        sink.handle_event({
            "type": "round.main.completed",
            "input_tokens": self.engineer_in,
            "output_tokens": self.engineer_out,
        })
        sink.handle_event({
            "type": "round.review.completed",
            "input_tokens": self.reviewer_in,
            "output_tokens": self.reviewer_out,
        })
        return self.response_factory(objective, prelude_context)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _mk_memory(tmp_path: Path) -> LifeMemory:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    return mem


# ---------- _price_for / _CostTrackingSink --------------------------------

def test_price_for_known_models() -> None:
    assert _price_for("gpt-5.4") == (1.25, 10.0)
    assert _price_for("gpt-5.4-mini") == (0.25, 2.0)


def test_price_for_unknown_falls_back() -> None:
    # Unknown 'mini' name → mini family
    assert _price_for("acme-mini-1") == (0.25, 2.0)
    # Other unknown → gpt-5.4 family
    assert _price_for("acme-grand-1") == (1.25, 10.0)


def test_cost_tracking_sink_aggregates_and_forwards() -> None:
    inner = _RecordingSink()
    s = _CostTrackingSink(inner, engineer_model="gpt-5.4-mini", reviewer_model="gpt-5.4")
    s.handle_event({"type": "round.main.completed", "input_tokens": 1000, "output_tokens": 500})
    s.handle_event({"type": "round.review.completed", "input_tokens": 100, "output_tokens": 50})
    s.handle_event({"type": "other"})  # forwarded, not counted
    # Forwarded events.
    assert len(inner.events) == 3
    # Tokens.
    assert s.engineer_input_tokens == 1000
    assert s.engineer_output_tokens == 500
    assert s.reviewer_input_tokens == 100
    assert s.reviewer_output_tokens == 50
    # Cost: 1000*0.25/1e6 + 500*2.0/1e6 + 100*1.25/1e6 + 50*10.0/1e6
    expected = (1000 * 0.25 + 500 * 2.0 + 100 * 1.25 + 50 * 10.0) / 1_000_000
    assert s.total_usd() == pytest.approx(expected)


def test_cost_sink_tolerates_malformed_event() -> None:
    inner = _RecordingSink()
    s = _CostTrackingSink(inner, engineer_model="gpt-5.4-mini", reviewer_model="gpt-5.4")
    s.handle_event({"type": "round.main.completed", "input_tokens": "garbage"})
    # No crash, no count.
    assert s.engineer_input_tokens == 0


# ---------- LifeBudget -----------------------------------------------------

def test_budget_remaining_today_starts_full(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    b = LifeBudget(daily_cap_usd=5.0)
    assert b.remaining_today(mem.journal) == pytest.approx(5.0)


def test_budget_subtracts_today_cost(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    mem.journal.append(JournalEntry.new(kind="x", title="t", summary="s", cost_usd=2.0))
    b = LifeBudget(daily_cap_usd=5.0)
    assert b.remaining_today(mem.journal) == pytest.approx(3.0)


def test_budget_can_start_blocks_oversize_item(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    b = LifeBudget(per_mission_cap_usd=1.0, daily_cap_usd=5.0)
    item = BacklogItem.new(title="big", objective="...", max_cost_usd=2.0)
    ok, reason = b.can_start(item=item, journal=mem.journal)
    assert not ok
    assert "per-mission cap" in reason


def test_budget_can_start_blocks_when_daily_exhausted(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    mem.journal.append(JournalEntry.new(kind="x", title="t", summary="s", cost_usd=4.5))
    b = LifeBudget(per_mission_cap_usd=1.0, daily_cap_usd=5.0)
    item = BacklogItem.new(title="next", objective="...", max_cost_usd=1.0)
    ok, reason = b.can_start(item=item, journal=mem.journal)
    assert not ok
    assert "daily budget" in reason


def test_budget_can_start_allows_within_caps(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    b = LifeBudget(per_mission_cap_usd=1.0, daily_cap_usd=5.0)
    item = BacklogItem.new(title="ok", objective="...", max_cost_usd=0.5)
    ok, _ = b.can_start(item=item, journal=mem.journal)
    assert ok


# ---------- LifeSupervisor end-to-end -------------------------------------

def _mk_sup(
    tmp_path: Path,
    *,
    runner: _FakeRunner | None = None,
    sink: _RecordingSink | None = None,
    budget: LifeBudget | None = None,
    poll_interval: float = 0.01,
) -> tuple[LifeSupervisor, _RecordingSink, _FakeRunner, LifeMemory]:
    mem = _mk_memory(tmp_path)
    runner = runner or _FakeRunner()
    sink = sink or _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=budget or LifeBudget(),
        poll_interval_seconds=poll_interval,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)
    return sup, sink, runner, mem


def test_tick_returns_none_when_backlog_empty(tmp_path: Path) -> None:
    sup, _, runner, _ = _mk_sup(tmp_path)
    assert sup.tick() is None
    assert runner.calls == []


def test_tick_runs_one_mission_and_journals(tmp_path: Path) -> None:
    sup, sink, runner, mem = _mk_sup(tmp_path)
    item = mem.backlog.add(BacklogItem.new(title="do thing", objective="do the thing"))
    result = sup.tick()
    assert result is not None
    assert result["success"] is True
    assert result["item_id"] == item.id
    # Backlog row marked done.
    rows = mem.backlog.all()
    assert rows[0].status == "done"
    # Exactly one journal entry, kind=mission_complete.
    entries = mem.journal.all()
    assert len(entries) == 1
    assert entries[0].kind == "mission_complete"
    assert entries[0].title == "do thing"
    assert entries[0].cost_usd > 0
    # Sink got both life events.
    types = {e.get("type") for e in sink.events}
    assert "life.mission.started" in types
    assert "life.mission.completed" in types
    # Runner saw an empty prelude (no identity yet has user data; default
    # identity is seeded though, so prelude could be non-empty if the
    # objective overlaps with the identity card. Just assert it's a string).
    assert isinstance(runner.calls[0]["prelude_context"], str)


def test_tick_records_failure(tmp_path: Path) -> None:
    runner = _FakeRunner(
        response_factory=lambda obj, p: _FakeOutcome(
            success=False, status="incomplete", stop_reason="ran out", rounds=2
        )
    )
    sup, _, _, mem = _mk_sup(tmp_path, runner=runner)
    item = mem.backlog.add(BacklogItem.new(title="fail-task", objective="..."))
    sup.tick()
    rows = mem.backlog.all()
    assert rows[0].status == "failed"
    assert "ran out" in rows[0].last_error
    entries = mem.journal.all()
    assert entries[0].kind == "mission_failed"


def test_tick_records_exception_path(tmp_path: Path) -> None:
    runner = _FakeRunner(raise_on_objective="kaboom")
    sup, _, _, mem = _mk_sup(tmp_path, runner=runner)
    item = mem.backlog.add(BacklogItem.new(title="x", objective="kaboom"))
    sup.tick()
    rows = mem.backlog.all()
    assert rows[0].status == "failed"
    assert "RuntimeError: boom" in rows[0].last_error
    entries = mem.journal.all()
    assert entries[0].kind == "mission_failed"


def test_run_processes_priority_order(tmp_path: Path) -> None:
    sup, _, runner, mem = _mk_sup(tmp_path)
    mem.backlog.add(BacklogItem.new(title="low", objective="low task", priority=200))
    mem.backlog.add(BacklogItem.new(title="hi", objective="hi task", priority=10))
    mem.backlog.add(BacklogItem.new(title="mid", objective="mid task", priority=100))
    sup.run()
    objectives = [c["objective"] for c in runner.calls]
    assert objectives == ["hi task", "mid task", "low task"]
    assert all(it.status == "done" for it in mem.backlog.all())


def test_max_missions_cap_stops_run(tmp_path: Path) -> None:
    budget = LifeBudget(per_mission_cap_usd=10.0, daily_cap_usd=100.0, max_missions=2)
    sup, _, runner, mem = _mk_sup(tmp_path, budget=budget)
    for i in range(5):
        mem.backlog.add(BacklogItem.new(title=f"t{i}", objective=f"o{i}"))
    summary = sup.run()
    assert summary["missions_started"] == 2
    assert len(runner.calls) == 2
    # Remaining 3 still pending.
    pending = [it for it in mem.backlog.all() if it.status == "pending"]
    assert len(pending) == 3


def test_budget_block_pauses_supervisor(tmp_path: Path) -> None:
    # per-mission cap below item cap → block on first.
    budget = LifeBudget(per_mission_cap_usd=0.5, daily_cap_usd=100.0, max_missions=10)
    sup, _, runner, mem = _mk_sup(tmp_path, budget=budget)
    mem.backlog.add(BacklogItem.new(title="big", objective="...", max_cost_usd=2.0))
    summary = sup.run()
    # Runner never called.
    assert runner.calls == []
    # Item still pending (we didn't fail it; we paused).
    rows = mem.backlog.all()
    assert rows[0].status == "pending"
    # Journal has one budget_pause entry.
    assert any(e.kind == "budget_pause" for e in mem.journal.all())


def test_stop_event_short_circuits_run(tmp_path: Path) -> None:
    ev = threading.Event()
    ev.set()  # signal *before* run
    cfg = LifeSupervisorConfig(stop_event=ev, poll_interval_seconds=0.01)
    mem = _mk_memory(tmp_path)
    runner = _FakeRunner()
    sup = LifeSupervisor(
        memory=mem, runner=runner, sink=_RecordingSink(), config=cfg
    )
    mem.backlog.add(BacklogItem.new(title="t", objective="..."))
    summary = sup.run()
    assert summary["missions_started"] == 0
    assert runner.calls == []


def test_prelude_threaded_into_runner(tmp_path: Path) -> None:
    sup, _, runner, mem = _mk_sup(tmp_path)
    # Seed a relevant journal entry so prelude is non-trivial.
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="Database migration helper",
            summary="created migrate_users.py",
            tags=["database"],
        )
    )
    mem.backlog.add(BacklogItem.new(
        title="db",
        objective="add another database migration for orders",
    ))
    sup.tick()
    prelude = runner.calls[0]["prelude_context"]
    assert "non-authoritative" in prelude.lower()
    assert "Database migration helper" in prelude


def test_cost_recorded_in_journal_entry(tmp_path: Path) -> None:
    sup, _, _, mem = _mk_sup(tmp_path)
    mem.backlog.add(BacklogItem.new(title="t", objective="objective text"))
    sup.tick()
    entry = mem.journal.all()[0]
    # Cost > 0; matches what _CostTrackingSink would compute.
    assert entry.cost_usd > 0
    assert "tokens_in=1200" in entry.summary or "tokens_in=1200;" in entry.summary
