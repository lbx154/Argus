from __future__ import annotations

from pathlib import Path

from argus_skill.team import task_board as tb


def _form(root: Path) -> None:
    tb.form(root, [
        {"task_id": "a", "title": "A", "objective": "do a", "owns_paths": ["a/**"]},
        {"task_id": "b", "title": "B", "objective": "do b", "owns_paths": ["b/**"], "deps": ["a"]},
    ])


def test_claim_returns_pending_and_flips_state(tmp_path: Path) -> None:
    _form(tmp_path)
    got = tb.claim(tmp_path, "tm-1", now=100.0)
    assert got is not None and got["task_id"] == "a"
    assert got["owner"] == "tm-1" and got["state"] == "claimed"


def test_dep_blocks_claim_until_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0)            # claims "a"
    assert tb.claim(tmp_path, "tm-2", now=2.0) is None  # "b" still blocked by "a"
    tb.complete(tmp_path, "a", shard="shards/a.jsonl")
    got = tb.claim(tmp_path, "tm-2", now=3.0)
    assert got is not None and got["task_id"] == "b"


def test_no_double_claim(tmp_path: Path) -> None:
    _form(tmp_path)
    first = tb.claim(tmp_path, "tm-1", now=1.0)
    second = tb.claim(tmp_path, "tm-2", now=1.0)
    assert first["task_id"] == "a"
    assert second is None or second["task_id"] != "a"


def test_reassign_stale_returns_to_pending(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=1.0)
    reassigned = tb.reassign_stale(tmp_path, ttl=10.0, now=100.0)
    assert reassigned == ["a"]
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["state"] == "pending" and snap["a"]["attempts"] == 1
    # a fresh heartbeat is NOT reassigned
    tb.claim(tmp_path, "tm-2", now=200.0)
    tb.heartbeat(tmp_path, "a", now=205.0)
    assert tb.reassign_stale(tmp_path, ttl=100.0, now=210.0) == []


def test_all_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0)
    tb.complete(tmp_path, "a")
    assert tb.all_done(tmp_path) is False
    tb.claim(tmp_path, "tm-2", now=2.0)
    tb.complete(tmp_path, "b")
    assert tb.all_done(tmp_path) is True


def test_claim_specific_no_crossing(tmp_path: Path) -> None:
    # two members each claim their OWN assigned task; never cross (the spawn-race fix)
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "do a", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "do b", "owns_paths": ["b/**"]},
    ])
    ga = tb.claim_specific(tmp_path, "a", "w1", now=1.0)
    gb = tb.claim_specific(tmp_path, "b", "w2", now=1.0)
    assert ga["task_id"] == "a" and ga["owner"] == "w1"
    assert gb["task_id"] == "b" and gb["owner"] == "w2"


def test_claim_specific_rejects_taken_or_missing(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x", "owns_paths": ["a/**"]}])
    assert tb.claim_specific(tmp_path, "a", "w1", now=1.0)["owner"] == "w1"
    assert tb.claim_specific(tmp_path, "a", "w2", now=2.0) is None   # already taken
    assert tb.claim_specific(tmp_path, "nope", "w3", now=3.0) is None  # missing


def test_form_stores_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
    ])
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["priority"] == 100   # default
    assert snap["b"]["priority"] == 5


def test_claim_top_orders_by_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"], "priority": 100},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
        {"task_id": "c", "objective": "z", "owns_paths": ["c/**"], "priority": 5},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "b"   # lowest number first
    assert tb.claim_top(tmp_path, "w2", now=2.0)["task_id"] == "c"   # tie -> task_id order
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w4", now=4.0) is None             # backlog empty


def test_claim_top_respects_deps(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"], "priority": 100},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 1, "deps": ["a"]},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "a"   # b blocked despite priority 1
    assert tb.claim_top(tmp_path, "w2", now=2.0) is None
    tb.complete(tmp_path, "a")
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "b"


def test_count_in_flight(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"]},
    ])
    assert tb.count_in_flight(tmp_path) == 0
    tb.claim_top(tmp_path, "w1", now=1.0)     # a -> claimed
    assert tb.count_in_flight(tmp_path) == 1
    tb.heartbeat(tmp_path, "a", now=1.0)       # claimed -> running
    assert tb.count_in_flight(tmp_path) == 1
    tb.complete(tmp_path, "a")                 # running -> done
    assert tb.count_in_flight(tmp_path) == 0
