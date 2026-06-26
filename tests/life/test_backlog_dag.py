"""Dependency-DAG scheduling for the backlog.

Covers the upgrade from a flat priority queue to a topologically-scheduled
DAG: ``claim_next`` only hands out items whose ``deps`` are all ``done``,
dead dependencies cascade-skip instead of wedging the queue, and the
no-deps path is provably unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.memory import Backlog, BacklogItem


# ---------- schema: deps field ---------------------------------------------

def test_new_defaults_to_empty_deps() -> None:
    it = BacklogItem.new(title="t", objective="o")
    assert it.deps == []


def test_new_accepts_deps() -> None:
    it = BacklogItem.new(title="t", objective="o", deps=["a", "b"])
    assert it.deps == ["a", "b"]
    # ``new`` must copy, not alias, the caller's list.
    src = ["x"]
    it2 = BacklogItem.new(title="t", objective="o", deps=src)
    src.append("y")
    assert it2.deps == ["x"]


def test_deps_roundtrip_through_jsonable() -> None:
    it = BacklogItem.new(title="t", objective="o", deps=["dep1", "dep2"])
    restored = BacklogItem.from_jsonable(it.to_jsonable())
    assert restored.deps == ["dep1", "dep2"]


def test_legacy_row_without_deps_loads_as_no_deps() -> None:
    # A pre-DAG jsonl row has no "deps" key at all.
    legacy = {
        "id": "abc123",
        "ts": 1700000000.0,
        "title": "old",
        "objective": "old objective",
        "status": "pending",
        "priority": 100,
        "max_cost_usd": 1.0,
        "tags": [],
        "notes": "",
    }
    restored = BacklogItem.from_jsonable(legacy)
    assert restored.deps == []


# ---------- no-deps behaviour is unchanged ---------------------------------

def test_claim_next_no_deps_matches_priority_order(tmp_path: Path) -> None:
    # Identical to the pre-DAG flat backlog: priority then ts.
    b = Backlog(tmp_path / "backlog.jsonl")
    b.add(BacklogItem.new(title="low", objective="...", priority=200))
    hi = b.add(BacklogItem.new(title="hi", objective="...", priority=10))
    b.add(BacklogItem.new(title="mid", objective="...", priority=100))

    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.id == hi.id
    assert claimed.status == "running"
    # next_pending mirrors the ready head; hi is now running, so mid is next.
    head = b.next_pending()
    assert head is not None and head.title == "mid"


def test_claim_next_empty_backlog_is_none(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    assert b.claim_next() is None
    assert b.next_pending() is None


# ---------- chain A -> B ---------------------------------------------------

def test_chain_dependency_blocks_until_done(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # First claim must be A; B is gated behind A.
    first = b.claim_next()
    assert first is not None and first.id == a.id
    # B is not ready while A is only running.
    assert b.next_pending() is None
    assert b.claim_next() is None

    # Finish A; now B becomes claimable.
    b.mark_done(a.id)
    second = b.claim_next()
    assert second is not None and second.id == bee.id


# ---------- fan-in: C depends on A and B -----------------------------------

def test_fan_in_waits_for_all_deps(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="..."))
    c = b.add(BacklogItem.new(title="C", objective="...", deps=[a.id, bee.id]))

    # Two claims hand out A and B (order doesn't matter); C is gated.
    claimed1 = b.claim_next()
    claimed2 = b.claim_next()
    assert claimed1 is not None and claimed2 is not None
    assert {claimed1.id, claimed2.id} == {a.id, bee.id}
    assert b.claim_next() is None  # C still blocked (no dep done)

    # One dep done is not enough.
    b.mark_done(a.id)
    assert b.claim_next() is None

    # Both deps done -> C is claimable.
    b.mark_done(bee.id)
    third = b.claim_next()
    assert third is not None and third.id == c.id


# ---------- dead-dependency cascade ----------------------------------------

def test_failed_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # A fails -> B can never satisfy its deps.
    b.mark_failed(a.id, error="boom")
    # claim_next runs the cascade: B is marked skipped, and nothing ready.
    assert b.claim_next() is None

    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "skipped"
    assert "did not complete" in rows["B"].last_error
    assert a.id in rows["B"].last_error
    # B is no longer pending, so it cannot be claimed.
    assert b.next_pending() is None


def test_skipped_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))
    b.update(a.id, status="skipped")

    assert b.claim_next() is None
    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "skipped"


def test_missing_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    b.add(BacklogItem.new(title="B", objective="...", deps=["does-not-exist"]))
    assert b.claim_next() is None
    rows = b.all()
    assert rows[0].status == "skipped"
    assert "does not exist" in rows[0].last_error


def test_cascade_does_not_touch_items_with_live_deps(tmp_path: Path) -> None:
    # A still pending (not terminal) -> B stays pending, not cascaded.
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="...", priority=10))
    bee = b.add(BacklogItem.new(title="B", objective="...", priority=5, deps=[a.id]))

    # B has higher priority but is blocked; A is claimed first.
    claimed = b.claim_next()
    assert claimed is not None and claimed.id == a.id
    # B is untouched, still pending.
    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "pending"
    assert rows["B"].last_error == ""


def test_self_and_cyclic_deps_never_ready_but_do_not_crash(tmp_path: Path) -> None:
    # A depends on itself; X<->Y cycle. None are ever ready (no dep is done),
    # none are terminal-non-done, so they are not cascaded either: they just
    # sit pending and claim_next returns None — indistinguishable from "no
    # work", which the daemon's idle path already handles.
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    b.update(a.id, deps=[a.id])  # self-dependency
    x = b.add(BacklogItem.new(title="X", objective="..."))
    y = b.add(BacklogItem.new(title="Y", objective="..."))
    b.update(x.id, deps=[y.id])
    b.update(y.id, deps=[x.id])

    assert b.claim_next() is None
    assert b.next_pending() is None
    # All three remain pending (not skipped, not crashed).
    assert all(it.status == "pending" for it in b.all())


# ---------- ready() vs pending() -------------------------------------------

def test_pending_lists_blocked_but_ready_does_not(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # pending() shows both (display/status); ready() shows only A.
    assert {it.id for it in b.pending()} == {a.id, bee.id}
    assert [it.id for it in b.ready()] == [a.id]
