"""Tests for ``LifeSupervisor`` and ``LifeBudget``.

Uses a fake mission runner that emits the same shape of events the real
``MissionExecutor`` does, so we can verify cost tracking / journal
entries / budget gating without spinning up codex.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from argus_skill.core.models import RunnerResult
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

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        return

    def close(self) -> None:
        return


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


def test_remaining_today_reuses_cached_history_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mem = _mk_memory(tmp_path)
    rows = [{"ts": 9999999999.0, "cost_usd": 2.0}]
    calls = {"n": 0}

    def _fake_history(path):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("remaining_today rescanned full history")
        return rows

    monkeypatch.setattr("argus_skill.life.memory._read_jsonl_history", _fake_history)
    b = LifeBudget(daily_cap_usd=10.0)

    first = b.remaining_today(mem.journal, now=1_700_000_000.0)
    second = b.remaining_today(mem.journal, now=1_700_000_000.0)

    assert first == pytest.approx(8.0)
    assert second == pytest.approx(8.0)
    assert calls["n"] == 1


def test_rotation_preserves_remaining_today_across_journal_rollover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("argus_skill.life.memory.Journal.ROTATE_BYTES", 256)
    mem = _mk_memory(tmp_path)
    mem.journal.append(
        JournalEntry.new(kind="x", title="old", summary="o" * 300, cost_usd=2.0)
    )
    mem.journal.append(JournalEntry.new(kind="x", title="mid", summary="m", cost_usd=3.0))
    mem.journal.append(JournalEntry.new(kind="x", title="new", summary="n", cost_usd=1.0))

    assert (tmp_path / "journal.jsonl.1").exists()
    b = LifeBudget(daily_cap_usd=10.0)
    assert b.remaining_today(mem.journal) == pytest.approx(4.0)


def test_budget_can_start_does_not_block_oversize_item(tmp_path: Path) -> None:
    """Per-mission cap is no longer a hard refusal: a 7×24 daemon should
    keep working even if an item's ``max_cost_usd`` exceeds the
    operator-set cap. The supervisor caps the *effective* mission spend
    via the daily cap envelope instead.
    """
    mem = _mk_memory(tmp_path)
    b = LifeBudget(per_mission_cap_usd=1.0, daily_cap_usd=5.0)
    item = BacklogItem.new(title="big", objective="...", max_cost_usd=2.0)
    ok, reason = b.can_start(item=item, journal=mem.journal)
    assert ok, f"oversize item should now run, but was blocked: {reason}"


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


def test_budget_defaults_are_long_run_friendly() -> None:
    b = LifeBudget()
    assert b.per_mission_cap_usd == 30.0
    assert b.daily_cap_usd == 180.0
    assert b.max_missions == 6


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
    # Mission start is journaled before the terminal completion row.
    entries = mem.journal.all()
    assert [entry.kind for entry in entries] == ["mission_started", "mission_complete"]
    assert entries[1].title == "do thing"
    assert entries[1].cost_usd > 0
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
    mem.backlog.add(BacklogItem.new(title="fail-task", objective="..."))
    sup.tick()
    rows = mem.backlog.all()
    assert rows[0].status == "failed"
    assert "ran out" in rows[0].last_error
    entries = mem.journal.all()
    assert [entry.kind for entry in entries] == ["mission_started", "mission_failed"]


def test_tick_records_exception_path(tmp_path: Path) -> None:
    runner = _FakeRunner(raise_on_objective="kaboom")
    sup, _, _, mem = _mk_sup(tmp_path, runner=runner)
    mem.backlog.add(BacklogItem.new(title="x", objective="kaboom"))
    sup.tick()
    rows = mem.backlog.all()
    assert rows[0].status == "failed"
    assert "RuntimeError: boom" in rows[0].last_error
    entries = mem.journal.all()
    assert [entry.kind for entry in entries] == ["mission_started", "mission_failed"]


def test_run_processes_priority_order(tmp_path: Path) -> None:
    sup, _, runner, mem = _mk_sup(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="low", objective="low task", priority=200, iterate=False,
    ))
    mem.backlog.add(BacklogItem.new(
        title="hi", objective="hi task", priority=10, iterate=False,
    ))
    mem.backlog.add(BacklogItem.new(
        title="mid", objective="mid task", priority=100, iterate=False,
    ))
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


def test_budget_block_pauses_supervisor_on_daily_exhaustion(tmp_path: Path) -> None:
    """Per-mission cap is no longer a refusal trigger; only daily-cap
    exhaustion still pauses the supervisor.
    """
    # Pre-spend half the daily budget so the *next* item can't fit.
    budget = LifeBudget(per_mission_cap_usd=10.0, daily_cap_usd=1.0, max_missions=10)
    sup, _, runner, mem = _mk_sup(tmp_path, budget=budget)
    mem.journal.append(JournalEntry.new(kind="x", title="t", summary="s", cost_usd=0.5))
    mem.backlog.add(BacklogItem.new(title="next", objective="...", max_cost_usd=1.0))
    sup.run()
    assert runner.calls == []
    rows = mem.backlog.all()
    assert rows[0].status == "pending"
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


def test_critic_journal_tail_uses_tail_not_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sup, _, _, mem = _mk_sup(tmp_path)
    entries = [
        JournalEntry.new(
            kind="mission_complete",
            title=f"hit-{i}",
            summary=f"summary {i}",
            extra={"item_id": "current"},
        )
        for i in range(6)
    ]
    tail_calls: list[int] = []

    def _tail(n: int = 20):
        tail_calls.append(n)
        return entries

    monkeypatch.setattr(mem.journal, "tail", _tail)
    monkeypatch.setattr(
        mem.journal,
        "all",
        lambda: pytest.fail("critic render must not read the full journal"),
    )

    out = sup._render_recent_journal_for_critic("current")

    assert tail_calls == [6]
    assert "summary 3" in out
    assert "summary 5" in out


def test_planner_journal_tail_uses_tail_not_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sup, _, _, mem = _mk_sup(tmp_path)
    entries = [
        JournalEntry.new(kind="planner_cycle", title=f"cycle-{i}", summary=f"summary {i}")
        for i in range(20)
    ]
    tail_calls: list[int] = []

    def _tail(n: int = 20):
        tail_calls.append(n)
        return entries

    monkeypatch.setattr(mem.journal, "tail", _tail)
    monkeypatch.setattr(
        mem.journal,
        "all",
        lambda: pytest.fail("planner render must not read the full journal"),
    )

    out = sup._render_journal_for_planner()

    assert tail_calls == [20]
    assert "cycle-19" in out
    assert "summary 0" in out


def test_cost_recorded_in_journal_entry(tmp_path: Path) -> None:
    sup, _, _, mem = _mk_sup(tmp_path)
    mem.backlog.add(BacklogItem.new(title="t", objective="objective text"))
    sup.tick()
    entry = mem.journal.all()[-1]
    # Cost > 0; matches what _CostTrackingSink would compute.
    assert entry.cost_usd > 0
    assert "tokens_in=1200" in entry.summary or "tokens_in=1200;" in entry.summary


# ---------------------------------------------------------------------------
# Mechanism: orphan reaper + no double execution
# ---------------------------------------------------------------------------

def test_supervisor_startup_reaps_orphaned_running(tmp_path: Path) -> None:
    """A previous process crashed mid-mission, leaving status=running.
    A fresh supervisor must mark it failed (not silently re-run it,
    not silently ignore it forever)."""
    mem = _mk_memory(tmp_path)
    item = mem.backlog.add(BacklogItem.new(title="crashy", objective="crashy"))
    mem.backlog.mark_running(item.id)  # simulate prior crash mid-flight

    runner = _FakeRunner()
    sink = _RecordingSink()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(poll_interval_seconds=0.01),
    )

    # Reaper ran in __init__: item is failed, an orphan event was emitted,
    # and a journal entry was written.
    assert mem.backlog.all()[0].status == "failed"
    assert any(e.get("type") == "life.mission.orphaned" for e in sink.events)
    kinds = {e.kind for e in mem.journal.all()}
    assert "mission_orphaned" in kinds

    # And — most importantly — the runner is NOT invoked for it.
    assert sup.tick() is None
    assert runner.calls == []


def test_completed_mission_cannot_be_re_executed_by_supervisor(tmp_path: Path) -> None:
    """End-to-end mechanism check: once a mission has completed, no
    sequence of ticks can re-execute it."""
    sup, _, runner, mem = _mk_sup(tmp_path)
    item = mem.backlog.add(BacklogItem.new(title="once", objective="run once"))
    sup.tick()
    assert mem.backlog.all()[0].status == "done"
    assert len(runner.calls) == 1

    # Many follow-up ticks must not invoke the runner again.
    for _ in range(5):
        assert sup.tick() is None
    assert len(runner.calls) == 1

    # And the completed item is sealed against any external attempt to
    # flip it back to pending.
    from argus_skill.life.memory import IllegalStateTransition
    with pytest.raises(IllegalStateTransition):
        mem.backlog.update(item.id, status="pending")


# ---------------------------------------------------------------------------
# runtime_context injection
# ---------------------------------------------------------------------------

def test_runtime_context_injected_into_prelude(tmp_path: Path) -> None:
    """When ``LifeSupervisorConfig.runtime_context`` is set, the runner
    receives it as part of the prelude_context."""
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(title="t1", objective="say hi"))
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=1),
        runtime_context="## Runtime info\n- Backend: codex\n- Model: gpt-5.4-mini",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
    )
    sup.run()
    assert len(runner.calls) == 1
    prelude = runner.calls[0]["prelude_context"]
    assert "Runtime info" in prelude
    assert "codex" in prelude


def test_runtime_context_empty_no_injection(tmp_path: Path) -> None:
    """When runtime_context is empty, prelude is unchanged."""
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(title="t2", objective="say bye"))
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=1),
        runtime_context="",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
    )
    sup.run()
    prelude = runner.calls[0]["prelude_context"]
    assert "Runtime info" not in prelude


# ---------------------------------------------------------------------------
# auth failure stops supervisor
# ---------------------------------------------------------------------------

def test_auth_failure_stops_supervisor(tmp_path: Path) -> None:
    """When the runner returns an outcome with ``auth_failure=True``, the
    supervisor must stop and include ``stopped_by='auth_failure'`` in
    the summary."""

    @dataclass
    class _AuthFailOutcome(_FakeOutcome):
        auth_failure: bool = True

    runner = _FakeRunner(
        response_factory=lambda obj, pre: _AuthFailOutcome(),
    )
    mem = _mk_memory(tmp_path)
    # Add two items — only the first should run.
    mem.backlog.add(BacklogItem.new(title="a1", objective="first"))
    mem.backlog.add(BacklogItem.new(title="a2", objective="second"))
    sink = _RecordingSink()
    stop_event = threading.Event()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=10),
        stop_event=stop_event,
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
    )
    summary = sup.run()
    assert summary["stopped_by"] == "auth_failure"
    assert len(runner.calls) == 1  # only 1 mission ran, then stopped
    assert stop_event.is_set()
    # Check that a life.auth_failure event was emitted
    auth_events = [e for e in sink.events if e.get("type") == "life.auth_failure"]
    assert len(auth_events) == 1


# ---------------------------------------------------------------------------
# continuous mode + planner
# ---------------------------------------------------------------------------

def test_continuous_mode_without_planner_stops_without_project_done(
    tmp_path: Path,
) -> None:
    sup, sink, runner, mem = _mk_sup(tmp_path)
    sup.config.continuous = True
    sup.config.continuous_objective = "optimize the project"
    mem.backlog.add(BacklogItem.new(
        title="initial", objective="do stuff", iterate=False,
    ))

    summary = sup.run()

    assert summary["stopped_by"] == "planner_unavailable"
    assert len(runner.calls) == 1
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert not any(
        event.get("type") == "life.planner.verdict"
        and event.get("project_done")
        for event in sink.events
    )


def test_continuous_mode_calls_planner_when_backlog_empty(tmp_path: Path) -> None:
    """In continuous mode, when the backlog empties after a mission, the
    supervisor should call the planner (via critic_runner) to generate
    new work. If planner says project_done, supervisor stops."""
    call_count = {"n": 0}

    def factory(obj: str, pre: str) -> _FakeOutcome:
        call_count["n"] += 1
        return _FakeOutcome()

    runner = _FakeRunner(response_factory=factory)
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="initial", objective="do stuff", iterate=False,
    ))
    sink = _RecordingSink()

    # Create a fake critic_runner that returns "project_done" JSON
    class _FakePlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            class _Result:
                agent_messages = ['{"project_done": true, "reason": "all done", "new_tasks": []}']
            return _Result()

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="optimize the project",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_FakePlannerRunner(),
    )
    summary = sup.run()
    assert summary["stopped_by"] == "project_done"
    assert call_count["n"] == 1  # 1 mission ran
    assert summary["planning_cycles"] == 1
    # Planner events should be emitted
    planner_events = [e for e in sink.events if e.get("type", "").startswith("life.planner")]
    assert len(planner_events) >= 2  # start + verdict


def test_continuous_mode_planner_generates_new_tasks(tmp_path: Path) -> None:
    """When the planner says not done and provides tasks, those tasks
    are added to the backlog and executed."""
    missions_run: list[str] = []

    def factory(obj: str, pre: str) -> _FakeOutcome:
        missions_run.append(obj)
        return _FakeOutcome()

    runner = _FakeRunner(response_factory=factory)
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="first", objective="task one", iterate=False,
    ))
    sink = _RecordingSink()

    # Planner returns new tasks on first call, then done on second
    planner_calls = {"n": 0}

    class _FakePlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            planner_calls["n"] += 1
            if planner_calls["n"] == 1:
                payload = (
                    '{"project_done": false, "reason": "needs more work", '
                    '"new_tasks": [{"title": "fix tests", "objective": "task two"}]}'
                )
            else:
                payload = '{"project_done": true, "reason": "all good now", "new_tasks": []}'

            class _Result:
                agent_messages = [payload]
            return _Result()

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="optimize the project",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_FakePlannerRunner(),
    )
    summary = sup.run()
    assert summary["stopped_by"] == "project_done"
    assert len(missions_run) == 2  # first task + planner-generated task
    assert missions_run[0] == "task one"
    assert missions_run[1] == "task two"
    # planner_calls: 1 for planning after task one, 1 for critic evaluate
    # on task two (iterate=True by default), 1 for planning after task two
    assert planner_calls["n"] == 3


def test_planner_budget_counts_planner_tokens(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="seed",
        objective="do the first task",
        iterate=False,
    ))
    sink = _RecordingSink()

    class _TokenPlannerBackend:
        def __init__(self) -> None:
            self.planner_calls = 0
            self.critic_calls = 0

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            if run_label.startswith("critic."):
                self.critic_calls += 1
                return RunnerResult(
                    exit_code=0,
                    agent_messages=['{"stop": true, "reason": "done", "improvements": []}'],
                    input_tokens=0,
                    output_tokens=0,
                )

            self.planner_calls += 1
            if self.planner_calls == 1:
                payload = (
                    '{"project_done": false, "reason": "needs more work", '
                    '"new_tasks": [{"title": "follow-up", "objective": "do task two"}]}'
                )
            else:
                payload = (
                    '{"project_done": true, "reason": "all good now", '
                    '"new_tasks": []}'
                )
            return RunnerResult(
                exit_code=0,
                agent_messages=[payload],
                input_tokens=750,
                output_tokens=250,
            )

    runner = _FakeRunner(
        engineer_in=0,
        engineer_out=0,
        reviewer_in=0,
        reviewer_out=0,
    )
    backend = _TokenPlannerBackend()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(per_mission_cap_usd=0.1, daily_cap_usd=0.5, max_missions=999),
        continuous=True,
        continuous_objective="optimize the project",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=backend,
    )

    summary = sup.run()

    planner_cost = (750 * 1.25 + 250 * 10.0) / 1_000_000
    entries = mem.journal.all()
    planner_entries = [e for e in entries if e.kind.startswith("planner")]

    assert summary["stopped_by"] == "project_done"
    assert summary["planning_cycles"] == 2
    assert backend.planner_calls == 2
    assert backend.critic_calls == 1
    assert len(planner_entries) == 2
    assert planner_entries[0].kind == "planner_cycle"
    assert planner_entries[1].kind == "planner_done"
    assert planner_entries[0].cost_usd == pytest.approx(planner_cost)
    assert planner_entries[1].cost_usd == pytest.approx(planner_cost)
    assert LifeBudget(daily_cap_usd=0.5).remaining_today(mem.journal) == pytest.approx(
        0.5 - (planner_cost * 2)
    )


def test_non_continuous_mode_exits_on_empty_backlog(tmp_path: Path) -> None:
    """Without continuous mode, supervisor exits when backlog is empty
    (existing behavior preserved)."""
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(title="only", objective="just one"))
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999),
        continuous=False,
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
    )
    summary = sup.run()
    # Should exit silently (no more pending work, not continuous)
    assert len(runner.calls) == 1
    assert summary["stopped_by"] in ("backlog_empty", "__silent_stop__")
