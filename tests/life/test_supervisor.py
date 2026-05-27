"""Tests for ``LifeSupervisor`` and ``LifeBudget``.

Uses a fake mission runner that emits the same shape of events the real
``MissionExecutor`` does, so we can verify cost tracking / journal
entries / budget gating without spinning up codex.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.runner import should_clear_thread_id_after_outcome
from argus_skill.life.memory import (
    BacklogItem,
    JournalEntry,
    LifeMemory,
    MemoryBundle,
)
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    _build_emnlp_finalization_objective,
    _CostTrackingSink,
    _is_emnlp_finalization_objective,
    _planner_emnlp_stage_hints,
    _planner_tasks_need_emnlp_finalization_override,
    _price_for,
    _select_emnlp_finalization_repair_task,
)
from argus_skill.skills.pipeline_contracts import ContractIssue

# ---------- helpers --------------------------------------------------------

@dataclass
class _FakeOutcome:
    success: bool = True
    status: str = "success"
    stop_reason: str = ""
    rounds: int = 1
    final_message: str = ""
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


def _planner_task(
    title: str,
    objective: str,
    *,
    impact_score: int = 4,
    impact_area: str = "reliability",
    evidence: str = "planner identified a high-value gap",
    scope: str | None = None,
) -> dict[str, object]:
    task = {
        "title": title,
        "objective": objective,
        "impact_score": impact_score,
        "impact_area": impact_area,
        "evidence": evidence,
    }
    if scope is not None:
        task["scope"] = scope
    return task


def test_session_poison_classifier_clears_no_progress_and_empty_output() -> None:
    assert should_clear_thread_id_after_outcome(status="no_progress", fatal_error=None)
    assert should_clear_thread_id_after_outcome(
        status="done",
        fatal_error=(
            "Codex ran out of room in the model's context window. "
            "Start a new thread or clear earlier history before retrying."
        ),
    )
    assert not should_clear_thread_id_after_outcome(status="done", fatal_error=None)
    assert not should_clear_thread_id_after_outcome(
        status="error",
        fatal_error="runner binary not found: codex",
    )
    assert not should_clear_thread_id_after_outcome(
        status="error",
        fatal_error="External interrupt: daemon stop requested",
    )
    assert not should_clear_thread_id_after_outcome(
        status="error",
        fatal_error="stream disconnected before completion: response.failed event received",
    )
    assert not should_clear_thread_id_after_outcome(
        status="error",
        fatal_error=(
            "Reconnecting... 100/100 "
            "(stream disconnected before completion: response.failed event received)"
        ),
    )


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
    s.handle_event({
        "type": "round.main.completed",
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 500,
    })
    s.handle_event({
        "type": "round.review.completed",
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 50,
    })
    s.handle_event({"type": "other"})  # forwarded, not counted
    # Forwarded events.
    assert len(inner.events) == 3
    # Tokens.
    assert s.engineer_input_tokens == 1000
    assert s.engineer_cached_input_tokens == 400
    assert s.engineer_output_tokens == 500
    assert s.reviewer_input_tokens == 100
    assert s.reviewer_cached_input_tokens == 25
    assert s.reviewer_output_tokens == 50
    # Cost: engineer input discount + reviewer input discount.
    expected = (
        (600 * 0.25)
        + (400 * 0.025)
        + (500 * 2.0)
        + (75 * 1.25)
        + (25 * 0.125)
        + (50 * 10.0)
    ) / 1_000_000
    assert s.total_usd() == pytest.approx(expected)


def test_cost_tracking_sink_diffs_explicit_cumulative_events() -> None:
    inner = _RecordingSink()
    s = _CostTrackingSink(inner, engineer_model="gpt-5.4-mini", reviewer_model="gpt-5.4")
    s.handle_event({
        "type": "round.main.completed",
        "session_id": "thread-1",
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 100,
        "usage_scope": "cumulative",
    })
    s.handle_event({
        "type": "round.main.completed",
        "session_id": "thread-1",
        "input_tokens": 1250,
        "cached_input_tokens": 500,
        "output_tokens": 130,
        "usage_scope": "cumulative",
    })

    assert len(inner.events) == 2
    assert s.engineer_input_tokens == 1250
    assert s.engineer_cached_input_tokens == 500
    assert s.engineer_output_tokens == 130


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


def test_planner_journal_context_is_project_scoped(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    bundle_a = MemoryBundle.for_cwd(repo_a, global_root=home)
    bundle_b = MemoryBundle.for_cwd(repo_b, global_root=home)
    bundle_a.init()
    bundle_b.init()

    bundle_b.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="stale beta success",
            summary="wrong workspace",
        )
    )
    bundle_a.journal.append(
        JournalEntry.new(
            kind="mission_failed",
            title="fresh alpha blocker",
            summary="right workspace",
        )
    )

    sup = LifeSupervisor(
        memory=bundle_a,
        runner=_FakeRunner(),
        sink=_RecordingSink(),
        config=LifeSupervisorConfig(),
    )

    out = sup._render_journal_for_planner()

    assert "fresh alpha blocker" in out
    assert "right workspace" in out
    assert "stale beta success" not in out
    assert "wrong workspace" not in out


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

    # Reaper ran in __init__: item is re-queued to pending (first retry),
    # an orphan recovery event was emitted, and a journal entry was written.
    assert mem.backlog.all()[0].status == "pending"
    assert mem.backlog.all()[0].orphan_retries == 1
    kinds = {e.kind for e in mem.journal.all()}
    assert "mission_requeued" in kinds

    # The item is now pending again — tick() WILL pick it up and execute it.
    # This is the desired behavior: daemon restart recovers the task.
    result = sup.tick()
    assert result is not None
    assert len(runner.calls) == 1


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


def test_project_workdir_prefers_env_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path / "memory")
    sink = _RecordingSink()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_WORKDIR", str(repo))
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(),
    )

    assert sup._project_workdir() == repo


def test_project_workdir_prefers_configured_worktree_over_memory_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    bundle = MemoryBundle.for_cwd(repo, global_root=tmp_path / "life")
    sink = _RecordingSink()
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_WORKDIR", str(tmp_path / "env"))
    sup = LifeSupervisor(
        memory=bundle,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(project_worktree=override),
    )

    assert sup._project_workdir() == override
    assert sup._planner_workdir() == override


def test_full_scale_evidence_precondition_blocks_premature_downstream_task(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path / "memory")
    sink = _RecordingSink()
    repo = tmp_path / "repo"
    repo.mkdir()
    item = mem.backlog.add(BacklogItem.new(
        title="Turn the evidence into an EMNLP paper package",
        objective=(
            "Start only after `python -m argus_skill.skills.pipeline_contracts "
            "validate-full-scale-evidence --project-root .` exits 0. "
            "Then draft the manuscript, reviews, and submission assurance."
        ),
        iterate=False,
    ))
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=repo,
            budget=LifeBudget(daily_cap_usd=999.0),
        ),
    )

    outcome = sup.tick()

    assert outcome is not None
    assert outcome["status"] == "precondition_blocked"
    assert runner.calls == []
    blocked = next(row for row in mem.backlog.all() if row.id == item.id)
    assert blocked.status == "failed"
    assert "validate-full-scale-evidence" in blocked.last_error
    assert "missing_full_scale_experiment_run" in blocked.last_error
    entry = mem.journal.all()[-1]
    assert entry.kind == "mission_failed"
    assert entry.extra["terminal_status"] == "precondition_blocked"
    assert entry.extra["precondition"] == "validate-full-scale-evidence"
    assert any(
        event.get("type") == "life.mission.precondition_blocked"
        and event.get("item_id") == item.id
        for event in sink.events
    )


def test_full_scale_evidence_acceptance_task_still_runs_when_gate_is_red(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner()
    mem = _mk_memory(tmp_path / "memory")
    sink = _RecordingSink()
    repo = tmp_path / "repo"
    repo.mkdir()
    mem.backlog.add(BacklogItem.new(
        title="Complete the full-scale EMNLP evidence gate",
        objective=(
            "Build or repair the experiment matrix, then run "
            "`python -m argus_skill.skills.pipeline_contracts "
            "validate-full-scale-evidence --project-root .`; acceptance requires "
            "that command to exit 0 before stopping."
        ),
        iterate=False,
    ))
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=repo,
            budget=LifeBudget(daily_cap_usd=999.0),
        ),
    )

    outcome = sup.tick()

    assert outcome is not None
    assert outcome["status"] == "success"
    assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# auth failure pauses current drain pass without killing daemon
# ---------------------------------------------------------------------------

def test_auth_failure_pauses_supervisor_without_setting_stop_event(tmp_path: Path) -> None:
    """When the runner returns an outcome with ``auth_failure=True``, the
    supervisor stops the current drain pass and reports the issue, but it
    must not set the daemon stop_event."""

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
    assert not stop_event.is_set()
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

    assert summary["stopped_by"] == "planner_error"
    assert len(runner.calls) == 1
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_error" for entry in mem.journal.all())
    assert not any(
        event.get("type") == "life.planner.verdict"
        and event.get("project_done")
        for event in sink.events
    )


def test_continuous_mode_planner_backend_exception_is_retryable(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    runner = _FakeRunner()

    class _BadPlannerRunner:
        def run_exec(self, **kwargs: Any) -> Any:  # noqa: ANN401
            del kwargs

            class _Result:
                input_tokens = 1
                cached_input_tokens = 0
                output_tokens = 1
                agent_messages = [
                    json.dumps(
                        {
                            "project_done": True,
                            "reason": "done",
                            "new_tasks": [
                                _planner_task(
                                    "bad follow-up",
                                    "should not be accepted",
                                )
                            ],
                        }
                    )
                ]

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
        critic_runner=_BadPlannerRunner(),
    )

    summary = sup.run()

    assert summary["stopped_by"] == "planner_error"
    assert runner.calls == []
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_error" for entry in mem.journal.all())
    assert any(
        event.get("type") == "life.planner.error"
        for event in sink.events
    )


def test_continuous_mode_planner_schema_violation_is_retryable(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    runner = _FakeRunner()

    class _BoomPlannerRunner:
        def run_exec(self, **kwargs: Any) -> Any:  # noqa: ANN401
            del kwargs
            raise RuntimeError("planner backend exploded")

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
        critic_runner=_BoomPlannerRunner(),
    )

    summary = sup.run()

    assert summary["stopped_by"] == "planner_error"
    assert runner.calls == []
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_error" for entry in mem.journal.all())
    assert any(
        event.get("type") == "life.planner.error"
        for event in sink.events
    )


def test_continuous_mode_open_ended_project_done_stays_enabled(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    runner = _FakeRunner()

    class _PlannerRunner:
        def run_exec(self, **kwargs: Any) -> Any:  # noqa: ANN401
            del kwargs

            class _Result:
                input_tokens = 11
                cached_input_tokens = 0
                output_tokens = 7
                agent_messages = [
                    json.dumps(
                        {
                            "project_done": True,
                            "reason": "self-improvement goal is complete for now",
                            "new_tasks": [],
                        }
                    )
                ]

            return _Result()

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="open-ended self-improvement goal",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_PlannerRunner(),
    )

    summary = sup.run()

    assert summary["stopped_by"] == "planner_retry"
    assert runner.calls == []
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_retry" for entry in mem.journal.all())
    assert any(
        event.get("type") == "life.planner.verdict"
        and event.get("open_ended_objective") is True
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


def test_continuous_mode_planner_cycle_gate_can_defer_planning(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    runner = _FakeRunner()
    gate_calls = 0

    def _gate() -> str:
        nonlocal gate_calls
        gate_calls += 1
        return "daemon_handoff"

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="optimize the project",
        planner_cycle_gate=_gate,
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=None,
    )

    summary = sup.run()

    assert summary["stopped_by"] == "daemon_handoff"
    assert summary["planning_cycles"] == 0
    assert gate_calls == 1
    assert runner.calls == []
    assert any(
        event.get("type") == "life.planner.deferred"
        and event.get("reason") == "daemon_handoff"
        for event in sink.events
    )


def test_post_mission_hook_can_trigger_daemon_handoff_between_missions(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(title="self-arch", objective="modify runtime"))
    mem.backlog.add(BacklogItem.new(title="next", objective="use new runtime"))
    sink = _RecordingSink()
    runner = _FakeRunner()
    hook_outcomes: list[dict[str, Any]] = []

    def _hook(outcome: dict[str, Any]) -> str:
        hook_outcomes.append(outcome)
        return "daemon_handoff"

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        post_mission_hook=_hook,
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
    )

    summary = sup.run()

    assert summary["stopped_by"] == "daemon_handoff"
    assert len(runner.calls) == 1
    rows = mem.backlog.all()
    assert rows[0].status == "done"
    assert rows[1].status == "pending"
    assert hook_outcomes and hook_outcomes[0]["title"] == "self-arch"
    assert any(
        event.get("type") == "life.post_mission.stop"
        and event.get("reason") == "daemon_handoff"
        for event in sink.events
    )


def test_continuous_mode_planner_can_request_daemon_handoff(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    runner = _FakeRunner()
    restart_reasons: list[str] = []

    def _restart(reason: str) -> bool:
        restart_reasons.append(reason)
        return True

    class _FakePlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            assert "Runtime source changed since daemon start" in prompt

            class _Result:
                agent_messages = [
                    (
                        '{"project_done": false, "reason": "fresh daemon needed", '
                        '"restart_daemon": true, '
                        '"restart_reason": "daemon lifecycle code changed", '
                        '"new_tasks": []}'
                    )
                ]

            return _Result()

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="optimize the project",
        planner_runtime_context_provider=lambda: (
            "Runtime source changed since daemon start."
        ),
        planner_restart_handler=_restart,
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

    assert summary["stopped_by"] == "daemon_handoff"
    assert summary["planning_cycles"] == 1
    assert restart_reasons == ["daemon lifecycle code changed"]
    assert runner.calls == []
    verdict = next(e for e in sink.events if e.get("type") == "life.planner.verdict")
    assert verdict["restart_daemon"] is True
    assert verdict["restart_reason"] == "daemon lifecycle code changed"


def test_continuous_mode_planner_receives_full_execution_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)

    runner = _FakeRunner(response_factory=lambda obj, pre: _FakeOutcome())
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="initial", objective="do stuff", iterate=False,
    ))
    sink = _RecordingSink()

    class _RecordingPlannerRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            self.calls.append({
                "prompt": prompt,
                "options": options,
                "run_label": run_label,
                "resume_thread_id": resume_thread_id,
            })

            class _Result:
                agent_messages = ['{"project_done": true, "reason": "all done", "new_tasks": []}']

            return _Result()

    critic = _RecordingPlannerRunner()
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
        critic_runner=critic,
    )

    summary = sup.run()

    assert summary["stopped_by"] == "project_done"
    assert len(critic.calls) == 1
    forwarded = critic.calls[0]["options"]
    assert forwarded.working_dir == str(tmp_path)
    assert forwarded.skip_git_repo_check is True
    assert forwarded.full_auto is False
    assert forwarded.dangerous_yolo is True


def test_continuous_mode_planner_receives_emnlp_gate_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Build an EMNLP paper and keep running validate-full-emnlp.\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(response_factory=lambda obj, pre: _FakeOutcome())
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()

    class _RecordingPlannerRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            del options, run_label, resume_thread_id, kw
            self.calls.append({"prompt": prompt})

            class _Result:
                agent_messages = [
                    json.dumps(
                        {
                            "project_done": False,
                            "reason": "missing the research pipeline",
                            "new_tasks": [
                                _planner_task(
                                    "bootstrap pipeline",
                                    "create research/PIPELINE_STATE.json",
                                    evidence="validate-full-emnlp reports missing_pipeline_state",
                                )
                            ],
                        }
                    )
                ]

            if len(self.calls) > 1:
                _Result.agent_messages = [
                    '{"project_done": true, "reason": "done enough", "new_tasks": []}'
                ]
            return _Result()

    critic = _RecordingPlannerRunner()
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
        critic_runner=critic,
    )

    summary = sup.run()

    assert summary["stopped_by"] == "project_done"
    assert "Automatic EMNLP final gate snapshot" in critic.calls[0]["prompt"]
    assert "missing_pipeline_state" in critic.calls[0]["prompt"]
    assert "this snapshot is not a PASS" in critic.calls[0]["prompt"]


def test_emnlp_gate_stage_hints_route_downstream_after_evidence() -> None:
    hint = _planner_emnlp_stage_hints([
        ContractIssue(
            "missing_stage_artifact",
            "paper/main.tex",
            "final EMNLP readiness requires this stage artifact",
        ),
        ContractIssue(
            "missing_submission_assurance",
            "paper/SUBMISSION_ASSURANCE.json",
            "submission assurance has not been written",
        ),
    ])

    assert "full-scale evidence is not currently a final-gate blocker" in hint
    assert "analysis/narrative/draft/review/submission artifacts" in hint
    assert "instead of relaunching duplicate benchmarks" in hint


def test_emnlp_gate_stage_hints_prioritize_full_scale_evidence() -> None:
    hint = _planner_emnlp_stage_hints([
        ContractIssue(
            "missing_full_scale_experiment_run",
            "experiments/",
            "no completed full-scale experiment run found",
        ),
        ContractIssue(
            "missing_stage_artifact",
            "paper/main.tex",
            "final EMNLP readiness requires this stage artifact",
        ),
    ])

    assert "complete or collect the full-scale evidence matrix" in hint
    assert "full-scale evidence is not currently a final-gate blocker" not in hint


def test_emnlp_gate_stage_hints_include_manifest_and_policy_helpers() -> None:
    hint = _planner_emnlp_stage_hints([
        ContractIssue(
            "artifact_digest_mismatch",
            "paper/main.tex",
            "artifact digest does not match the manifest",
        ),
        ContractIssue(
            "missing_validation_failure_route",
            "paper/VALIDATION_PRIORITY_POLICY.json:failure_routing.freshness",
            "failure_routing must define route 'freshness'",
        ),
    ])

    assert "refresh-manifest --project-root ." in hint
    assert "refresh-artifact-freshness --project-root ." in hint
    assert "write-validation-priority-policy --project-root ." in hint
    assert "repair-emnlp-contract-artifacts --project-root ." in hint


def test_emnlp_gate_stage_hints_route_image2_format_reviews_and_assurance() -> None:
    hint = _planner_emnlp_stage_hints([
        ContractIssue(
            "conceptual_body_figure_not_image2",
            "paper/main.tex",
            "body figure 1 uses a local redraw",
        ),
        ContractIssue(
            "table_caption_missing_number",
            "paper/main.tex",
            "caption must include the key numerical result",
        ),
        ContractIssue(
            "stale_academic_language_review_source",
            "paper/main.tex",
            "academic-language review hash is stale",
        ),
        ContractIssue(
            "stale_layout_review_artifact",
            "paper/main.pdf",
            "layout review hash is stale",
        ),
        ContractIssue(
            "submission_not_ready_verdict",
            "paper/SUBMISSION_ASSURANCE.json",
            "assurance verdict is FAIL",
        ),
    ])

    assert "image-2 issues belong to results-analysis/figures" in hint
    assert "exact accepted raster in main.tex" in hint
    assert "figure/table/format failures belong to format preflight" in hint
    assert "caption with a numerical or evidence-backed takeaway" in hint
    assert "rerun the model-backed `academic_language_review`" in hint
    assert "vision `paper_layout_review`" in hint
    assert "submission assurance is last" in hint


def test_emnlp_finalization_route_prioritizes_content_over_package_drift() -> None:
    route = _select_emnlp_finalization_repair_task([
        ContractIssue(
            "missing_stage_artifact",
            "paper/SUBMISSION_ASSURANCE.json",
            "submission artifact is missing",
        ),
        ContractIssue(
            "rendered_main_body_underfilled",
            "paper/main.pdf",
            "main body ends too early",
        ),
        ContractIssue(
            "artifact_digest_mismatch",
            "paper/main.tex",
            "manifest is stale",
        ),
    ])

    assert route is not None
    assert route.title == "Expand evidence-backed EMNLP content to final-paper length"
    assert "evidence gaps" in route.repair_focus
    objective = _build_emnlp_finalization_objective(route)
    assert "paper_optimization_task" in objective
    assert "Target issue codes: rendered_main_body_underfilled=1" in objective
    assert "not a broad paper rewrite" in objective
    assert "validate-full-scale-evidence --project-root ." in objective
    assert _is_emnlp_finalization_objective(objective)


def test_emnlp_finalization_route_targets_image2_before_generic_package() -> None:
    route = _select_emnlp_finalization_repair_task([
        ContractIssue(
            "missing_stage_artifact",
            "paper/main.tex",
            "paper artifact is missing",
        ),
        ContractIssue(
            "conceptual_body_figure_not_image2",
            "paper/main.tex",
            "overview is locally redrawn",
        ),
    ])

    assert route is not None
    assert route.title == "Repair the image-2 overview figure contract"
    objective = _build_emnlp_finalization_objective(route)
    assert "validate-image2-figures --project-root ." in objective
    assert "image-2" in objective


def test_emnlp_finalization_override_replaces_broad_paper_package_task() -> None:
    class _Task:
        title = "Build the evidence-backed EMNLP paper package"
        objective = "Run validate-full-emnlp and make the paper package better."
        evidence = "final gate is failing"

    assert _planner_tasks_need_emnlp_finalization_override(
        [_Task()],
        [
            ContractIssue(
                "rendered_main_body_underfilled",
                "paper/main.pdf",
                "main body ends too early",
            )
        ],
    )


def test_emnlp_finalization_override_keeps_specific_issue_task() -> None:
    class _Task:
        title = "Expand evidence-backed EMNLP content to final-paper length"
        objective = (
            "paper_optimization_task. Target issue codes: "
            "rendered_main_body_underfilled=1. Run validate-full-emnlp."
        )
        evidence = "rendered_main_body_underfilled at paper/main.pdf"

    assert not _planner_tasks_need_emnlp_finalization_override(
        [_Task()],
        [
            ContractIssue(
                "rendered_main_body_underfilled",
                "paper/main.pdf",
                "main body ends too early",
            )
        ],
    )


def test_continuous_mode_planner_refusal_falls_back_to_emnlp_gate_task(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Build an EMNLP paper and keep running validate-full-emnlp.\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(response_factory=lambda obj, pre: _FakeOutcome())
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()

    class _RefusingPlannerRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            del prompt, options, run_label, resume_thread_id, kw
            self.calls += 1

            class _Result:
                agent_messages = ["I'm sorry, but I cannot assist with that request."]

            if self.calls > 1:
                _Result.agent_messages = [
                    '{"project_done": true, "reason": "done enough", "new_tasks": []}'
                ]
            return _Result()

    critic = _RefusingPlannerRunner()
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
        critic_runner=critic,
    )

    summary = sup.run()

    assert summary["stopped_by"] == "project_done"
    assert runner.calls
    assert "Planner backend failed to return usable JSON" in runner.calls[0]["objective"]
    assert "missing_pipeline_state" in runner.calls[0]["objective"]
    assert not any(entry.kind == "planner_error" for entry in mem.journal.all())
    planned = next(entry for entry in mem.journal.all() if entry.kind == "planner_cycle")
    assert planned.extra["enqueued_titles"] == [
        "Bootstrap the grounded EMNLP research pipeline"
    ]


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
                payload = json.dumps({
                    "project_done": False,
                    "reason": "needs more work",
                    "new_tasks": [_planner_task("fix tests", "task two")],
                })
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
    planned = next(item for item in mem.backlog.all() if item.objective == "task two")
    assert planned.iteration_max_cycles == 6
    assert planned.iteration_budget_usd == pytest.approx(30.0)
    assert planned.tags == ["planner", "scope:bounded"]
    assert "- planner_scope: bounded" in runner.calls[1]["prelude_context"]
    assert "bounded_task" in runner.calls[1]["prelude_context"]
    # planner_calls: 1 for planning after task one, 1 for critic evaluate
    # on task two (iterate=True by default), 1 for planning after task two
    assert planner_calls["n"] == 3


def test_bounded_paper_task_metadata_uses_long_horizon_contract(tmp_path: Path) -> None:
    sup = LifeSupervisor(
        memory=_mk_memory(tmp_path),
        runner=_FakeRunner(),
        sink=_RecordingSink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="完成 EMNLP submission-ready paper",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
    )
    item = BacklogItem.new(
        title="Repair manuscript flow",
        objective="Fix paper/main.tex body length and citation flow.",
        tags=["planner", "scope:bounded"],
    )

    metadata = sup._render_backlog_item_metadata(item)

    assert "- planner_scope: bounded" in metadata
    assert "paper_optimization_task" in metadata
    assert "validate-research-md-format" in metadata
    assert "validate-full-emnlp" in metadata
    assert "one narrow check passed" in metadata


def test_emnlp_project_done_without_full_gate_enqueues_final_submission_task(
    tmp_path: Path,
) -> None:
    def factory(obj: str, pre: str) -> _FakeOutcome:  # noqa: ARG001
        outcome = _FakeOutcome()
        outcome.final_message = (
            "python -m argus_skill.skills.pipeline_contracts "
            "validate-full-emnlp --project-root .\nexit 0\nsuccess"
        )
        return outcome

    runner = _FakeRunner(response_factory=factory)
    mem = _mk_memory(tmp_path)
    mem.journal.append(
        JournalEntry.new(
            kind="mission_started",
            title="Prior final-proof attempt",
            summary=(
                "objective=Acceptance requires `validate-full-emnlp --project-root .` "
                "exits 0 before project_done."
            ),
            extra={
                "objective": (
                    "Acceptance requires `validate-full-emnlp --project-root .` "
                    "exits 0 before project_done."
                )
            },
        )
    )
    sink = _RecordingSink()
    planner_calls = {"n": 0}

    class _FakePlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):  # noqa: ARG002
            planner_calls["n"] += 1
            if run_label.startswith("critic."):
                payload = '{"stop": true, "reason": "full gate passed", "improvements": []}'
            else:
                payload = json.dumps({
                    "project_done": True,
                    "reason": "all good now",
                    "new_tasks": [],
                })

            class _Result:
                agent_messages = [payload]

            return _Result()

    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
        continuous=True,
        continuous_objective="完成 EMNLP 投稿实验并生成 submission-ready paper",
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
    assert len(runner.calls) == 1
    assert "Scope: final_submission" in runner.calls[0]["objective"]
    assert "validate-full-emnlp --project-root ." in runner.calls[0]["objective"]
    assert "validate-pipeline" in runner.calls[0]["objective"]
    final_item = next(
        item
        for item in mem.backlog.all()
        if item.title == "Prove EMNLP submission readiness"
    )
    assert final_item.tags == ["planner", "scope:final_submission"]
    assert "- planner_scope: final_submission" in runner.calls[0]["prelude_context"]
    assert "final_submission_gate" in runner.calls[0]["prelude_context"]
    assert mem.journal.all()[-1].kind == "planner_done"
    assert planner_calls["n"] == 3


def test_continuous_mode_planner_malformed_payload_is_retryable(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(response_factory=lambda obj, pre: _FakeOutcome())
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="initial", objective="do stuff", iterate=False,
    ))
    sink = _RecordingSink()

    class _BadPlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            class _Result:
                agent_messages = ["definitely not json"]
                input_tokens = 91
                cached_input_tokens = 9
                output_tokens = 4
            return _Result()

    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
            continuous=True,
            continuous_objective="optimize the project",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_BadPlannerRunner(),
    )

    summary = sup.run()

    assert summary["stopped_by"] == "planner_error"
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_error" for entry in mem.journal.all())


def test_continuous_mode_planner_backend_exception_after_mission_is_retryable(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(response_factory=lambda obj, pre: _FakeOutcome())
    mem = _mk_memory(tmp_path)
    mem.backlog.add(BacklogItem.new(
        title="initial", objective="do stuff", iterate=False,
    ))
    sink = _RecordingSink()

    class _BoomPlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            raise RuntimeError("planner exploded")

    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
            continuous=True,
            continuous_objective="optimize the project",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_BoomPlannerRunner(),
    )

    summary = sup.run()

    assert summary["stopped_by"] == "planner_error"
    assert all(entry.kind != "planner_done" for entry in mem.journal.all())
    assert any(entry.kind == "planner_error" for entry in mem.journal.all())


def test_continuous_mode_planner_skips_duplicate_tasks(tmp_path: Path) -> None:
    """Planner cycles should not enqueue tasks that are already pending
    or running; they should still enqueue genuinely new work."""

    class _PlannerBackend:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            payload = json.dumps({
                "project_done": False,
                "reason": "needs more work",
                "new_tasks": [
                    _planner_task("  RUNNING TASK  ", "ship unique stuff"),
                    _planner_task("Unique follow-up", "document the result"),
                ],
            })

            class _Result:
                agent_messages = [payload]
                input_tokens = 123
                output_tokens = 45

            return _Result()

    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    sup = LifeSupervisor(
        memory=mem,
        runner=_FakeRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
            continuous=True,
            continuous_objective="optimize the project",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_PlannerBackend(),
    )

    pending_item = mem.backlog.add(BacklogItem.new(
        title="pending task",
        objective="write docs",
        iterate=False,
    ))
    running_item = mem.backlog.add(BacklogItem.new(
        title="running task",
        objective="ship unique stuff",
        iterate=False,
    ))
    mem.backlog.update(running_item.id, status="running", started_ts=1.0)

    before_count = len(mem.backlog.all())
    planned = sup._plan_next_work()

    assert planned is True
    assert len(mem.backlog.all()) == before_count + 1

    rows = {item.title: item for item in mem.backlog.all()}
    assert rows[pending_item.title].status == "pending"
    assert rows[running_item.title].status == "running"
    assert rows["Unique follow-up"].status == "pending"
    assert "RUNNING TASK" not in {item.title for item in mem.backlog.all()}

    planner_events = [e for e in sink.events if e.get("type", "").startswith("life.planner")]
    assert any(e.get("type") == "life.planner.task_added" for e in planner_events)
    skipped = [e for e in planner_events if e.get("type") == "life.planner.task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["matched_item_id"] == running_item.id
    verdict = next(e for e in planner_events if e.get("type") == "life.planner.verdict")
    assert verdict["skipped_duplicate_tasks"] == 1
    assert verdict["enqueued_tasks"] == 1

    entry = mem.journal.all()[-1]
    assert entry.kind == "planner_cycle"
    assert "skipped 1 duplicate(s): RUNNING TASK" in entry.summary
    assert "enqueued 1 task(s): Unique follow-up" in entry.summary


def test_continuous_mode_planner_skips_completed_duplicate_tasks(tmp_path: Path) -> None:
    """Planner cycles should skip tasks that already finished and still
    enqueue genuinely new work."""

    class _PlannerBackend:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):
            payload = json.dumps({
                "project_done": False,
                "reason": "needs more work",
                "new_tasks": [
                    _planner_task("  FINISHED TASK  ", "ship unique stuff"),
                    _planner_task("Unique follow-up", "document the result"),
                ],
            })

            class _Result:
                agent_messages = [payload]
                input_tokens = 123
                output_tokens = 45

            return _Result()

    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    sup = LifeSupervisor(
        memory=mem,
        runner=_FakeRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
            continuous=True,
            continuous_objective="optimize the project",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=_PlannerBackend(),
    )

    finished_item = mem.backlog.add(BacklogItem.new(
        title="finished task",
        objective="ship unique stuff",
        iterate=False,
    ))
    mem.backlog.mark_done(finished_item.id)

    before_count = len(mem.backlog.all())
    planned = sup._plan_next_work()

    assert planned is True
    assert len(mem.backlog.all()) == before_count + 1

    rows = {item.title: item for item in mem.backlog.all()}
    assert rows[finished_item.title].status == "done"
    assert rows["Unique follow-up"].status == "pending"

    planner_events = [e for e in sink.events if e.get("type", "").startswith("life.planner")]
    skipped = [e for e in planner_events if e.get("type") == "life.planner.task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["matched_item_id"] == finished_item.id
    assert skipped[0]["matched_status"] == "done"
    assert skipped[0]["reason"] == "duplicate completed task"
    verdict = next(e for e in planner_events if e.get("type") == "life.planner.verdict")
    assert verdict["skipped_duplicate_tasks"] == 1
    assert verdict["enqueued_tasks"] == 1

    entry = mem.journal.all()[-1]
    assert entry.kind == "planner_cycle"
    assert "skipped 1 duplicate(s): FINISHED TASK" in entry.summary
    assert "enqueued 1 task(s): Unique follow-up" in entry.summary


def test_manual_rerun_of_completed_task_is_still_allowed(tmp_path: Path) -> None:
    mem = _mk_memory(tmp_path)
    finished = mem.backlog.add(BacklogItem.new(
        title="finished task",
        objective="ship unique stuff",
        iterate=False,
    ))
    mem.backlog.mark_done(finished.id)

    rerun = mem.backlog.add(BacklogItem.new(
        title="finished task",
        objective="ship unique stuff",
        iterate=False,
    ))

    assert rerun.id != finished.id
    items = [item for item in mem.backlog.all() if item.title == "finished task"]
    assert len(items) == 2
    assert {item.status for item in items} == {"done", "pending"}


def test_continuous_mode_planner_quarantines_recent_no_progress_repeat(
    tmp_path: Path,
) -> None:
    class _PlannerBackend:
        def __init__(self) -> None:
            self.planner_calls = 0
            self.critic_calls = 0

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None, **kw):  # noqa: ARG002
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
                payload = json.dumps({
                    "project_done": False,
                    "reason": "needs more work",
                    "new_tasks": [
                        _planner_task("Repeat task", "write report"),
                        _planner_task("Repeat task", "write report v2"),
                    ],
                })
            else:
                payload = json.dumps({
                    "project_done": True,
                    "reason": "all good now",
                    "new_tasks": [],
                })

            return RunnerResult(
                exit_code=0,
                agent_messages=[payload],
                input_tokens=123,
                output_tokens=45,
            )

    runner = _FakeRunner(
        response_factory=lambda objective, prelude: (  # noqa: ARG005
            _FakeOutcome(success=False, status="no_progress", stop_reason="stalled", rounds=2)
            if objective == "write report"
            else _FakeOutcome(success=True, status="done", stop_reason="", rounds=1)
        )
    )
    mem = _mk_memory(tmp_path)
    sink = _RecordingSink()
    planner_backend = _PlannerBackend()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=999, daily_cap_usd=999.0),
            continuous=True,
            continuous_objective="optimize the project",
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        critic_runner=planner_backend,
    )

    mem.backlog.add(BacklogItem.new(
        title="Repeat task",
        objective="write report",
        iterate=False,
    ))

    summary = sup.run()

    entries = mem.journal.all()
    failed = next(entry for entry in entries if entry.kind == "mission_failed")
    planned = next(entry for entry in entries if entry.kind == "planner_cycle")
    planner_events = [event for event in sink.events if event.get("type", "").startswith("life.planner")]
    skipped = [event for event in planner_events if event.get("type") == "life.planner.task_skipped"]

    assert summary["stopped_by"] == "project_done"
    assert summary["planning_cycles"] == 2
    assert planner_backend.planner_calls == 2
    assert planner_backend.critic_calls == 1
    assert [call["objective"] for call in runner.calls] == ["write report", "write report v2"]
    assert failed.extra["planner_task_signature"] == {
        "title": "repeat task",
        "objective": "write report",
    }
    assert failed.extra["terminal_status"] == "no_progress"
    assert failed.extra["stop_reason"] == "stalled"
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "recent no_progress failure"
    assert skipped[0]["skip_category"] == "recent_no_progress_failure"
    assert skipped[0]["matched_item_id"] == failed.extra["item_id"]
    assert skipped[0]["matched_title"] == "Repeat task"
    assert skipped[0]["matched_status"] == "no_progress"
    assert skipped[0]["matched_stop_reason"] == "stalled"
    assert skipped[0]["matched_signature"] == {
        "title": "repeat task",
        "objective": "write report",
    }
    assert planned.extra["skipped_recent_failure_tasks"] == 1
    assert planned.extra["skipped_recent_failure_titles"] == ["Repeat task"]
    assert "quarantined 1 recent no_progress repeat(s): Repeat task" in planned.summary
    assert any(item.objective == "write report v2" and item.status == "done" for item in mem.backlog.all())
    assert sum(1 for item in mem.backlog.all() if item.title == "Repeat task" and item.objective == "write report") == 1


def test_manual_backlog_rerun_is_not_blocked_by_recent_no_progress_failure(
    tmp_path: Path,
) -> None:
    mem = _mk_memory(tmp_path)
    mem.journal.append(
        JournalEntry.new(
            kind="mission_failed",
            title="Repeat task",
            summary="status=no_progress; reason=stalled",
            extra={
                "item_id": "mission-123",
                "objective": "write report",
                "planner_task_signature": {
                    "title": "repeat task",
                    "objective": "write report",
                },
                "terminal_status": "no_progress",
                "stop_reason": "stalled",
                "failure_reason": "stalled",
            },
        )
    )

    rerun = mem.backlog.add(BacklogItem.new(
        title="Repeat task",
        objective="write report",
        iterate=False,
    ))

    assert rerun.status == "pending"
    assert rerun.title == "Repeat task"
    assert rerun.objective == "write report"
    assert len([item for item in mem.backlog.all() if item.title == "Repeat task"]) == 1


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
                    cached_input_tokens=0,
                    output_tokens=0,
                )

            self.planner_calls += 1
            if self.planner_calls == 1:
                payload = json.dumps({
                    "project_done": False,
                    "reason": "needs more work",
                    "new_tasks": [_planner_task("follow-up", "do task two")],
                })
            else:
                payload = (
                    '{"project_done": true, "reason": "all good now", '
                    '"new_tasks": []}'
                )
            return RunnerResult(
                exit_code=0,
                agent_messages=[payload],
                input_tokens=750,
                cached_input_tokens=250,
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

    planner_cost = ((500 * 1.25) + (250 * 0.125) + (250 * 10.0)) / 1_000_000
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
