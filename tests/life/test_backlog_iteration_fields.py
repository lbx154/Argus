"""Backlog iteration-field schema tests: defaults, roundtrip, legacy rows."""
from __future__ import annotations

from pathlib import Path

from argus.life.memory import BacklogItem, LifeMemory


def test_backlog_requeue_refuses_terminal_items(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="x"))
    mem.backlog.mark_done(it.id)
    out = mem.backlog.requeue_for_iteration(
        it.id, new_objective="polish", cost_delta_usd=0.0
    )
    assert out is None  # cannot resurrect a done item


def test_backlog_stop_iteration_leaves_running_item_alone(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="x"))
    mem.backlog.update(it.id, status="running")
    stopped = mem.backlog.stop_iteration(it.id)
    assert stopped is not None
    assert stopped.iterate is False
    assert stopped.status == "running"  # supervisor will finalize after cycle
