from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.team import task_board as tb


def _form(root: Path) -> None:
    tb.form(root, [
        {"task_id": "a", "title": "A", "objective": "do a", "owns_paths": ["a/**"]},
        {
            "task_id": "b",
            "title": "B",
            "objective": "do b",
            "owns_paths": ["b/**"],
            "deps": ["a"],
        },
    ])


def test_claim_top_returns_pending_and_flips_state(tmp_path: Path) -> None:
    _form(tmp_path)
    got = tb.claim_top(tmp_path, "tm-1", now=100.0)
    assert got is not None and got["task_id"] == "a"
    assert got["owner"] == "tm-1" and got["state"] == "claimed"


def test_dependency_blocks_claim_until_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim_top(tmp_path, "tm-1", now=1.0)
    assert tb.claim_top(tmp_path, "tm-2", now=2.0) is None
    tb.complete(tmp_path, "a", shard="shards/a.jsonl")
    got = tb.claim_top(tmp_path, "tm-2", now=3.0)
    assert got is not None and got["task_id"] == "b"


def test_claim_top_never_double_claims(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    first = tb.claim_top(tmp_path, "tm-1", now=1.0)
    second = tb.claim_top(tmp_path, "tm-2", now=1.0)
    assert first is not None and first["task_id"] == "a"
    assert second is None


def test_reassign_stale_returns_to_pending(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim_top(tmp_path, "tm-1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=1.0)
    reassigned = tb.reassign_stale(tmp_path, ttl=10.0, now=100.0)
    assert reassigned == ["a"]
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["state"] == "pending" and snap["a"]["attempts"] == 1

    tb.claim_top(tmp_path, "tm-2", now=200.0)
    tb.heartbeat(tmp_path, "a", now=205.0)
    assert tb.reassign_stale(tmp_path, ttl=100.0, now=210.0) == []


@pytest.mark.parametrize("task_id", ["", ".", "..", "../escape", "nested/task", r"nested\task"])
def test_task_ids_cannot_escape_task_storage(tmp_path: Path, task_id: str) -> None:
    with pytest.raises(ValueError, match="invalid task_id"):
        tb.form(tmp_path, [{"task_id": task_id, "objective": "bad"}])
    assert not (tmp_path / "escape.json").exists()
    assert not any((tmp_path / "tasks").glob("*.json"))


def test_windows_unsafe_logical_id_round_trips_without_unsafe_filename(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "team::task", "objective": "portable"}])

    assert [task["task_id"] for task in tb.snapshot(tmp_path)] == ["team::task"]
    if __import__("os").name == "nt":
        assert all(":" not in path.name for path in (tmp_path / "tasks").iterdir())


def test_form_stores_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
    ])
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["priority"] == 100
    assert snap["b"]["priority"] == 5


def test_claim_top_orders_by_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "priority": 100},
        {"task_id": "b", "objective": "y", "priority": 5},
        {"task_id": "c", "objective": "z", "priority": 5},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "b"
    assert tb.claim_top(tmp_path, "w2", now=2.0)["task_id"] == "c"
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w4", now=4.0) is None


def test_claim_top_respects_dependencies_before_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "priority": 100},
        {"task_id": "b", "objective": "y", "priority": 1, "deps": ["a"]},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w2", now=2.0) is None
    tb.complete(tmp_path, "a")
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "b"


def test_count_in_flight(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x"},
        {"task_id": "b", "objective": "y"},
    ])
    assert tb.count_in_flight(tmp_path) == 0
    tb.claim_top(tmp_path, "w1", now=1.0)
    assert tb.count_in_flight(tmp_path) == 1
    tb.heartbeat(tmp_path, "a", now=1.0)
    assert tb.count_in_flight(tmp_path) == 1
    tb.complete(tmp_path, "a")
    assert tb.count_in_flight(tmp_path) == 0


def test_form_preserves_live_ownership_on_reform(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "v1", "priority": 100}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=2.0)

    tb.form(tmp_path, [{"task_id": "a", "objective": "v2-updated", "priority": 5}])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["state"] == "running" and task["owner"] == "w1"
    assert task["heartbeat_ts"] == 2.0
    assert task["objective"] == "v2-updated" and task["priority"] == 5
    assert tb.count_in_flight(tmp_path) == 1


def test_form_deliberately_reopens_terminal_task(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.complete(tmp_path, "a")
    tb.form(tmp_path, [{"task_id": "a", "objective": "x2"}])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["state"] == "pending"
    assert task["owner"] == "" and task["objective"] == "x2"
