from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.team import curator as cur
from argus_skill.team import leaderboard, pool, registry, roster, task_board


# --- restart durability: adopt orphans the prior daemon left running --------
def test_pid_is_teammate_verifies_real_cmdline(tmp_path: Path) -> None:
    import subprocess
    import sys
    # a live process whose argv looks like a teammate for member w42
    p = subprocess.Popen([sys.executable, "-c",
                          "import time; time.sleep(30)  # argus_skill.team.teammate_entry w42"])
    try:
        for _ in range(50):  # wait for exec so /proc cmdline is populated
            if cur._pid_is_teammate(p.pid, "w42"):
                break
            time.sleep(0.05)
        assert cur._pid_is_teammate(p.pid, "w42") is True
        assert cur._pid_is_teammate(p.pid, "w99") is False   # wrong member id
        assert cur._pid_is_teammate(2_000_000_000, "w42") is False  # dead pid
    finally:
        p.kill()
        p.wait()


def test_adopt_reclaims_running_roster_orphan_once(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "team"
    roster.add_member(root, {"id": "w1", "pid": 4242, "worktree": str(tmp_path),
                             "task_id": "t::a", "status": "running"})
    monkeypatch.setattr(cur, "_pid_is_teammate", lambda pid, mid: pid == 4242)
    c = _fake_curator(tmp_path)
    assert c._adopt_orphans(root, now=100.0) == ["w1"]
    assert "w1" in c._children and c.live_owner_ids(root) == {"w1"}
    # idempotent: a second pass does not re-adopt
    assert c._adopt_orphans(root, now=200.0) == []


def test_adopt_then_stop_kills_real_orphan(tmp_path: Path) -> None:
    import subprocess
    import sys
    root = tmp_path / "team"
    p = subprocess.Popen([sys.executable, "-c",
                          "import time; time.sleep(60)  # argus_skill.team.teammate_entry w7"],
                         start_new_session=True)
    roster.add_member(root, {"id": "w7", "pid": p.pid, "task_id": "t::a", "status": "running"})
    c = _fake_curator(tmp_path)
    for _ in range(50):  # wait for exec so cmdline-verified adoption can match
        if cur._pid_is_teammate(p.pid, "w7"):
            break
        time.sleep(0.05)
    assert c._adopt_orphans(root) == ["w7"]
    try:
        c.stop()  # must kill the adopted orphan, not just tracked Popen children
        assert p.wait(timeout=5) is not None
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_adopt_skips_dead_or_finished_members(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "team"
    roster.add_member(root, {"id": "dead", "pid": 1, "task_id": "t::a", "status": "running"})
    roster.add_member(root, {"id": "done", "pid": 4242, "task_id": "t::b", "status": "done"})
    monkeypatch.setattr(cur, "_pid_is_teammate", lambda pid, mid: pid == 4242)  # 4242 alive
    c = _fake_curator(tmp_path)
    assert c._adopt_orphans(root) == []  # dead pid + non-running status → neither adopted
    assert c._children == {}


def test_tick_adopts_orphans_so_no_duplicate_spawn(tmp_path: Path, monkeypatch) -> None:
    """The orphan bug: a restart that loses tracking re-spawns on tasks already
    running. Adopting the orphan makes it a live owner, so refill won't duplicate."""
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=1, state="running")
    task_board.form(root, [{"task_id": "t::a", "objective": "x"}])
    task_board.claim_top(root, "w1", now=100.0)  # the orphan already owns t::a
    roster.add_member(root, {"id": "w1", "pid": 4242, "task_id": "t::a", "status": "running"})
    monkeypatch.setattr(cur, "_pid_is_teammate", lambda pid, mid: pid == 4242)
    c = _fake_curator(tmp_path)
    c._tick(now=500.0)  # past ttl: would reassign+respawn if orphan were invisible
    assert c.live_owner_ids(root) == {"w1"}
    assert task_board.count_in_flight(root) == 1  # NOT 2 — no duplicate on t::a


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


def test_refill_uses_per_task_cwd_else_campaign(tmp_path: Path) -> None:
    """A task carrying its own ``cwd`` is spawned in that dir (independent per-kernel
    workdirs); a task without one falls back to the shared campaign cwd (legacy)."""
    root = tmp_path / "team"
    wd_a = tmp_path / "kernels" / "a"
    task_board.form(root, [
        {"task_id": "t::a", "objective": "x", "priority": 1, "cwd": str(wd_a)},
        {"task_id": "t::b", "objective": "x", "priority": 2},  # no cwd
    ])
    seen: dict[str, str] = {}

    def make_proc(root_, member_id, task_id, cwd):  # noqa: ANN001
        seen[task_id] = str(cwd)
        return FakeProc()

    c = cur.Curator(project_root=tmp_path, make_proc=make_proc)
    campaign = tmp_path / "campaign"
    c._refill(root, width=5, cwd=campaign, now=100.0)
    assert seen["t::a"] == str(wd_a)         # per-task cwd honored
    assert seen["t::b"] == str(campaign)     # legacy fallback to campaign cwd
    # and the roster records each child's real workdir
    members = {m["task_id"]: m["worktree"] for m in roster.members(root)}
    assert members["t::a"] == str(wd_a) and members["t::b"] == str(campaign)


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


def _seed_board(root: Path, target: str = "kA", metric: float = 1.2, mechanism: str = "fuse") -> None:
    d = root / "shards"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{mechanism}.jsonl").write_text(json.dumps(
        {"target": target, "metric": metric, "mechanism": mechanism, "success": True}) + "\n",
        encoding="utf-8")
    leaderboard.fold(root)


def test_distill_writes_strategy_from_leaderboard(tmp_path: Path) -> None:
    root = tmp_path / "team"
    _seed_board(root, "kA", 1.2, "fuse")
    captured: dict = {}

    def fake(prompt: str) -> str:
        captured["p"] = prompt
        return "## Strategy\nPrioritize kA: deepen the fused kernel."

    c = _fake_curator(tmp_path)
    assert c._distill_root(root, fake) is True
    strat = (root / "strategy.md").read_text(encoding="utf-8")
    assert "Prioritize kA" in strat
    assert "kA" in captured["p"] and "fuse" in captured["p"]  # leaderboard facts reach the LLM


def test_distill_degrades_to_prior_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "team"
    root.mkdir(parents=True, exist_ok=True)
    (root / "strategy.md").write_text("PRIOR", encoding="utf-8")
    _seed_board(root)

    def boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    c = _fake_curator(tmp_path)
    assert c._distill_root(root, boom) is False  # never raises
    assert (root / "strategy.md").read_text(encoding="utf-8") == "PRIOR"  # prior kept


def test_distill_noop_without_leaderboard(tmp_path: Path) -> None:
    root = tmp_path / "team"
    called: list = []
    c = _fake_curator(tmp_path)
    assert c._distill_root(root, lambda p: called.append(p) or "x") is False
    assert called == []  # nothing to distill → LLM not even called


def test_distill_skips_empty_response(tmp_path: Path) -> None:
    root = tmp_path / "team"
    _seed_board(root)
    (root / "strategy.md").write_text("PRIOR", encoding="utf-8")
    c = _fake_curator(tmp_path)
    assert c._distill_root(root, lambda p: "   ") is False
    assert (root / "strategy.md").read_text(encoding="utf-8") == "PRIOR"


def test_tick_distills_at_interval_when_fn_present(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    pool.update(root, width=0, state="running")  # width 0 → focus on distill, no spawns
    _seed_board(root, "kA", 1.0, "fuse")
    calls: list = []

    def fake(prompt: str) -> str:
        calls.append(prompt)
        return "## Strat\nkA: deepen"

    c = cur.Curator(project_root=tmp_path, make_proc=lambda *a, **k: FakeProc(),
                    distill_fn=fake, distill_interval_s=100.0)
    c._tick(now=1000.0)
    assert len(calls) == 1 and (root / "strategy.md").exists()
    c._tick(now=1050.0)            # within interval → no distill
    assert len(calls) == 1
    c._tick(now=1200.0)            # past interval → distill again
    assert len(calls) == 2


def test_tick_no_distill_without_fn(tmp_path: Path) -> None:
    root = tmp_path / "team"
    registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    _seed_board(root)
    c = _fake_curator(tmp_path)  # no distill_fn → deterministic fold only
    c._tick(now=1000.0)
    assert not (root / "strategy.md").exists()
