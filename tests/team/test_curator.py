from __future__ import annotations

import os
import json
import time
from pathlib import Path

from argus_skill.team import curator as cur
from argus_skill.team import leaderboard, pool, registry, roster, task_board


def test_spawn_tracked_records_real_child_and_roster_then_stop_reaps(tmp_path: Path) -> None:
    root = tmp_path / "team"
    c = cur.Curator(project_root=tmp_path, exec_cmd="sleep 60")
    tt = None
    try:
        pid = c._spawn_tracked(root, member_id="w1", task_id="t::a", cwd=tmp_path)
        assert pid > 0
        # tracked: the Curator retains the handle, so it OWNS the child
        assert "w1" in c._children
        tt = c._children["w1"]
        assert tt.member_id == "w1" and tt.task_id == "t::a"
        assert tt.proc.poll() is None  # alive
        # own session (own process group) so per-child killpg can't hit the daemon
        assert os.getpgid(pid) == pid
        # projected onto the roster (no heartbeat field)
        m = next(m for m in roster.members(root) if m["id"] == "w1")
        assert m["pid"] == pid and "heartbeat_ts" not in m
    finally:
        c.stop()
    # stop() terminated the tracked child
    assert tt is not None and tt.proc.poll() is not None


# --- deterministic logic tests: a fake process (no real subprocess) ---------
class FakeProc:
    _next_pid = 90000

    def __init__(self) -> None:
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self._rc: int | None = None

    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        if self._rc is None:
            self._rc = -15  # simulate SIGTERM taking effect
        return self._rc

    def exit(self, rc: int = 0) -> None:
        self._rc = rc


def _fake_curator(tmp_path: Path, **kw) -> cur.Curator:
    return cur.Curator(project_root=tmp_path,
                       make_proc=lambda *a, **k: FakeProc(), **kw)


def test_refill_fills_to_width_then_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": f"t::{i}", "objective": "x", "priority": i}
                           for i in range(5)])
    c = _fake_curator(tmp_path)
    res = c._refill(root, width=3, cwd=tmp_path, now=100.0)
    assert len(res["spawned"]) == 3
    assert task_board.count_in_flight(root) == 3
    assert len(c.live_owner_ids(root)) == 3
    # pool already full → a second refill spawns nothing
    assert c._refill(root, width=3, cwd=tmp_path, now=101.0)["spawned"] == []


def test_refill_stops_when_backlog_empty(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    res = c._refill(root, width=5, cwd=tmp_path, now=100.0)
    assert len(res["spawned"]) == 1  # only one task available


def test_refill_reassigns_dead_owner_then_refills(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    c._refill(root, width=1, cwd=tmp_path, now=100.0)
    (tt,) = c._children.values()
    tt.proc.exit(1)  # the child died → no longer a live owner
    # heartbeat was stamped at claim (100); at 400 with ttl 120 the task is stale
    res = c._refill(root, width=1, cwd=tmp_path, now=400.0, ttl=120.0)
    assert res["reassigned"] == ["t::a"]
    assert len(res["spawned"]) == 1  # a fresh teammate claims the freed task


def test_refill_does_not_reassign_a_live_owner(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    c._refill(root, width=1, cwd=tmp_path, now=100.0)
    # child still alive; even past ttl its task must NOT be reassigned (no double-run)
    res = c._refill(root, width=1, cwd=tmp_path, now=400.0, ttl=120.0)
    assert res["reassigned"] == [] and res["spawned"] == []


def test_reap_drops_exited_children(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    c._refill(root, width=1, cwd=tmp_path, now=100.0)
    (tt,) = c._children.values()
    tt.proc.exit(0)  # finished cleanly; teammate_entry already wrote its shard
    res = c._reap(now=200.0)
    assert res["dropped"] == [tt.member_id] and res["hard_killed"] == []
    assert c._children == {}


def test_reap_hard_timeout_killpg_and_fails_task(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    killed: list = []
    monkeypatch.setattr(cur.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(cur.os, "getpgid", lambda pid: pid)
    c = cur.Curator(project_root=tmp_path, make_proc=lambda *a, **k: FakeProc(),
                    teammate_timeout_s=10.0, hard_grace_s=5.0)
    c._refill(root, width=1, cwd=tmp_path, now=100.0)
    (tt,) = c._children.values()
    # alive (FakeProc) but now (200) is past the hard deadline (100+10+5=115)
    res = c._reap(now=200.0)
    assert res["hard_killed"] == [tt.member_id]
    assert killed  # killpg was invoked on the wedged child
    # BUG-3 fix: the task is freed IMMEDIATELY (no lost shard / stuck "running")
    task = next(t for t in task_board.snapshot(root) if t["task_id"] == "t::a")
    assert task["state"] == "failed"
    assert c._children == {}


def test_reap_keeps_alive_child_within_deadline(tmp_path: Path) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = cur.Curator(project_root=tmp_path, make_proc=lambda *a, **k: FakeProc(),
                    teammate_timeout_s=1000.0, hard_grace_s=100.0)
    c._refill(root, width=1, cwd=tmp_path, now=100.0)
    res = c._reap(now=150.0)  # well within deadline
    assert res == {"dropped": [], "hard_killed": []}
    assert len(c._children) == 1


# --- M1.4: tick / discover-from-registry / start-stop thread ----------------
def test_tick_refills_active_root_from_marker(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=2, state="running")
    task_board.form(root, [{"task_id": f"t::{i}", "objective": "x"} for i in range(3)])
    c = _fake_curator(tmp_path)
    c._tick(now=100.0)
    assert task_board.count_in_flight(root) == 2
    assert len(c.live_owner_ids(root)) == 2


def test_tick_uses_default_width_when_pool_unset(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    task_board.form(root, [{"task_id": f"t::{i}", "objective": "x"} for i in range(5)])
    c = _fake_curator(tmp_path, default_width=3)
    c._tick(now=100.0)  # no pool.json → default width 3
    assert task_board.count_in_flight(root) == 3


def test_tick_draining_stops_refill_and_removes_empty_marker(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, state="draining")
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    c._tick(now=100.0)
    assert task_board.count_in_flight(root) == 0  # draining never spawns
    assert registry.list_markers(tmp_path) == []  # empty campaign → marker removed


def test_tick_draining_keeps_marker_while_children_live(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=1, state="running")
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = _fake_curator(tmp_path)
    c._tick(now=100.0)
    assert len(c.live_owner_ids(root)) == 1
    pool.update(root, state="draining")
    c._tick(now=101.0)  # child still alive → keep the marker
    assert registry.list_markers(tmp_path) and len(c.live_owner_ids(root)) == 1
    # child finishes cleanly (teammate_entry would mark the task done) → next tick removes marker
    (tt,) = [t for t in c._children.values() if t.root == root]
    tt.proc.exit(0)
    task_board.complete(root, tt.task_id)
    c._tick(now=102.0)
    assert registry.list_markers(tmp_path) == []


def test_start_then_stop_runs_ticks_and_reaps_real_child(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=1, state="running")
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    c = cur.Curator(project_root=tmp_path, exec_cmd="sleep 60", tick_s=0.05)
    c.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not c.live_owner_ids(root):
            time.sleep(0.05)
        assert c.live_owner_ids(root)  # the resident loop kept N in flight on its own clock
    finally:
        c.stop()
    assert c._children == {}  # stop() joined the thread and reaped every child


def test_tick_folds_leaderboard_when_shards_present(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=1, state="running")
    task_board.form(root, [{"task_id": "t::a", "objective": "x", "target": "kA"}])
    d = root / "shards"
    d.mkdir(parents=True, exist_ok=True)
    (d / "w.jsonl").write_text(json.dumps(
        {"target": "kA", "metric": 2.0, "mechanism": "fuse", "success": True}) + "\n",
        encoding="utf-8")
    c = _fake_curator(tmp_path)
    c._tick(now=100.0)
    # the resident Curator maintains the leaderboard deterministically each tick
    assert leaderboard.read(root)["kA"]["best"] == {"mechanism": "fuse", "metric": 2.0}
