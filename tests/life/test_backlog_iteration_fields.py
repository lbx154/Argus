"""Backlog iteration-field schema tests: defaults, roundtrip, legacy rows."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.memory import BacklogItem, LifeMemory


def test_backlog_item_new_sets_iteration_defaults():
    it = BacklogItem.new(title="t", objective="ship a calculator")
    assert it.iterate is True
    assert it.iteration_max_cycles == 0
    assert it.iteration_cycles_done == 0
    assert it.iteration_cost_usd == 0.0
    assert it.original_objective == "ship a calculator"


def test_backlog_item_new_respects_once_kwarg():
    it = BacklogItem.new(title="t", objective="x", iterate=False)
    assert it.iterate is False
    assert it.original_objective == "x"


def test_backlog_item_roundtrip_preserves_iteration_fields():
    it = BacklogItem.new(
        title="t",
        objective="o",
        iterate=True,
        iteration_max_cycles=5,
    )
    it.iteration_cycles_done = 2
    it.iteration_cost_usd = 1.23
    blob = json.dumps(it.to_jsonable())
    restored = BacklogItem.from_jsonable(json.loads(blob))
    assert restored.iterate is True
    assert restored.iteration_max_cycles == 5
    assert restored.iteration_cycles_done == 2
    assert restored.iteration_cost_usd == 1.23
    assert restored.original_objective == "o"


def test_legacy_row_without_iteration_fields_loads_as_non_iterating():
    legacy = {
        "id": "abc123",
        "ts": 1700000000.0,
        "title": "old",
        "objective": "old objective",
        "status": "done",
        "priority": 100,
        "tags": [],
        "notes": "",
    }
    restored = BacklogItem.from_jsonable(legacy)
    # Backwards-compat: legacy rows default to iterate=False so old
    # done items are NOT resurrected for a polish pass when the
    # daemon starts up under the new code.
    assert restored.iterate is False
    assert restored.iteration_cycles_done == 0
    # original_objective falls back to objective on legacy rows.
    assert restored.original_objective == "old objective"


def test_backlog_requeue_for_iteration_bypasses_terminal_guard(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="initial"))
    mem.backlog.update(it.id, status="running")
    requeued = mem.backlog.requeue_for_iteration(
        it.id,
        new_objective="polish pass — add tests",
        cost_delta_usd=0.42,
    )
    assert requeued is not None
    assert requeued.status == "pending"
    assert requeued.objective == "polish pass — add tests"
    assert requeued.iteration_cycles_done == 1
    assert requeued.iteration_cost_usd == 0.42
    # original_objective MUST NOT be overwritten by the polish text.
    assert requeued.original_objective == "initial"
    assert requeued.started_ts is None


def test_backlog_requeue_refuses_terminal_items(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="x"))
    mem.backlog.mark_done(it.id)
    out = mem.backlog.requeue_for_iteration(
        it.id, new_objective="polish", cost_delta_usd=0.0
    )
    assert out is None  # cannot resurrect a done item


def test_backlog_stop_iteration_finalizes_pending_item(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="x"))
    stopped = mem.backlog.stop_iteration(it.id, reason="user changed mind")
    assert stopped is not None
    assert stopped.iterate is False
    assert stopped.status == "done"
    assert "user changed mind" in stopped.notes


def test_backlog_stop_iteration_leaves_running_item_alone(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    it = mem.backlog.add(BacklogItem.new(title="t", objective="x"))
    mem.backlog.update(it.id, status="running")
    stopped = mem.backlog.stop_iteration(it.id)
    assert stopped is not None
    assert stopped.iterate is False
    assert stopped.status == "running"  # supervisor will finalize after cycle
