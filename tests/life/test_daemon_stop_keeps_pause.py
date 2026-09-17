"""A typed pause returned by the round loop stays a pause whatever its stop kind says."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig


def test_daemon_stop_during_a_backend_failure_hold_pauses_instead_of_failing(tmp_path: Path) -> None:
    """s-009c3ec3 (2026-09-16 03:33): the round loop returned paused_daemon_shutdown
    while waiting out a Reviewer backend failure, the mission's stop kind still
    said backend_unavailable, and the item was archived as failed and never resumed."""
    memory = LifeMemory.open(tmp_path)
    item = BacklogItem.new(title="写个iclr论文", objective="写个iclr论文")
    memory.backlog.add(item)
    memory.backlog.mark_running(item.id)
    supervisor = LifeSupervisor(
        memory=memory, runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=tmp_path),
        config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    state = SimpleNamespace(
        item=item, outcome=SimpleNamespace(final_review_status=""),
        status="paused_daemon_shutdown", stop_kind="backend_unavailable",
        stop_reason="The wait after a Reviewer backend failure ended early: daemon stop requested.",
        usage_summary=SimpleNamespace(pricing_status="known"), usd=0.0, known_usd=0.0,
        context_packet_path=None,
    )

    result = supervisor._maybe_pause_for_recoverable_stop(state)

    assert result is not None and result["status"] == "paused_daemon_shutdown"
    assert result["recoverable"] is True
    stored = next(row for row in memory.backlog.active() if row.id == item.id)
    assert stored.status == "paused_daemon_shutdown"


def test_unknown_status_with_unmapped_stop_kind_still_settles_normally(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path)
    item = BacklogItem.new(title="t", objective="o")
    memory.backlog.add(item)
    memory.backlog.mark_running(item.id)
    supervisor = LifeSupervisor(
        memory=memory, runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=tmp_path),
        config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    state = SimpleNamespace(
        item=item, outcome=SimpleNamespace(final_review_status=""),
        status="error", stop_kind="backend_unavailable", stop_reason="boom",
        usage_summary=SimpleNamespace(pricing_status="known"), usd=0.0, known_usd=0.0,
        context_packet_path=None,
    )
    assert supervisor._maybe_pause_for_recoverable_stop(state) is None
