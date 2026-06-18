from __future__ import annotations

import json
from pathlib import Path

from argus_skill.tools import team


def _call(capsys, *args: str) -> tuple[int, str]:
    rc = team.main(list(args))
    return rc, capsys.readouterr().out


def test_form_spawn_status_dissolve(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(
        {"task_id": "a", "title": "A", "objective": "echo hello", "owns_paths": ["a/**"]}
    ) + "\n", encoding="utf-8")

    rc, _ = _call(capsys, "form", "--root", str(root), "--team-id", "t1", "--tasks", str(tasks))
    assert rc == 0

    # spawn a stub teammate that exits immediately (no real codex)
    rc, out = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                    "--member-id", "tm-1", "--task-id", "a", "--exec-cmd", "true")
    assert rc == 0
    assert json.loads(out)["task_id"] == "a"

    rc, out = _call(capsys, "status", "--root", str(root))
    status = json.loads(out)
    assert any(m["id"] == "tm-1" for m in status["members"])
    assert any(t["task_id"] == "a" for t in status["tasks"])

    rc, _ = _call(capsys, "dissolve", "--root", str(root))
    assert rc == 0


def test_send_and_drain_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _call(capsys, "send", "--root", str(root), "--to", "tm-1", "--from", "lead", "--text", "ping")
    rc, out = _call(capsys, "drain", "--root", str(root), "--member-id", "tm-1")
    msgs = json.loads(out)
    assert msgs[0]["text"] == "ping" and msgs[0]["from"] == "lead"


def test_claim_and_reassign_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps({"task_id": "k0", "objective": "opt"}) + "\n", encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--tasks", str(tasks))
    rc, out = _call(capsys, "claim", "--root", str(root), "--member-id", "tm-1")
    assert json.loads(out)["task_id"] == "k0"
    # ttl=-1 -> any claimed task is stale regardless of sub-second timing
    rc, out = _call(capsys, "reassign", "--root", str(root), "--ttl", "-1")
    assert json.loads(out)["reassigned"] == ["k0"]


def test_spawn_claims_specific_task_no_crossing(tmp_path: Path, capsys) -> None:
    # the M1 bug: parallel spawn claimed next-pending, crossing member IDs.
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "t1::a", "objective": "kernel a", "owns_paths": ["a/**"]}) + "\n"
        + json.dumps({"task_id": "t1::b", "objective": "kernel b", "owns_paths": ["b/**"]}) + "\n",
        encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t1", "--tasks", str(tasks))
    # spawn w2->b first, then w1->a (reverse) — old next-pending claim would cross
    _, out_b = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                     "--member-id", "t1::w2", "--task-id", "t1::b", "--exec-cmd", "true")
    _, out_a = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                     "--member-id", "t1::w1", "--task-id", "t1::a", "--exec-cmd", "true")
    assert json.loads(out_b)["task_id"] == "t1::b" and json.loads(out_b)["claimed"] is True
    assert json.loads(out_a)["task_id"] == "t1::a" and json.loads(out_a)["claimed"] is True
    _, out = _call(capsys, "status", "--root", str(root))
    by_id = {t["task_id"]: t for t in json.loads(out)["tasks"]}
    assert by_id["t1::a"]["owner"] == "t1::w1"
    assert by_id["t1::b"]["owner"] == "t1::w2"


def test_refill_once_caps_and_idempotent(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": f"k{i}", "objective": "opt", "owns_paths": [f"k{i}/**"]}
                   for i in range(5)])
    calls = []
    def fake_spawn(r, *, member_id, task_id, cwd, exec_cmd=""):
        calls.append((member_id, task_id)); return 4242
    res = teamcli.refill_once(root, width=3, cwd=tmp_path, ttl=180.0, now=1.0, spawn_fn=fake_spawn)
    assert len(res["spawned"]) == 3 and len(calls) == 3
    assert tb.count_in_flight(root) == 3
    res2 = teamcli.refill_once(root, width=3, cwd=tmp_path, ttl=180.0, now=2.0, spawn_fn=fake_spawn)
    assert res2["spawned"] == [] and len(calls) == 3       # idempotent when full


def test_refill_once_drains_short_backlog(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]},
                   {"task_id": "k1", "objective": "opt", "owns_paths": ["k1/**"]}])
    res = teamcli.refill_once(root, width=10, cwd=tmp_path, ttl=180.0, now=1.0,
                              spawn_fn=lambda r, **k: 1)
    assert len(res["spawned"]) == 2                          # only 2 tasks exist


def test_refill_once_reassigns_then_fills(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]}])
    tb.claim_top(root, "w0", now=1.0)                        # k0 claimed by a (now dead) teammate
    tb.heartbeat(root, "k0", now=1.0)
    # ttl small -> k0 is stale; refill should reassign it and re-spawn
    res = teamcli.refill_once(root, width=1, cwd=tmp_path, ttl=0.0, now=100.0,
                              spawn_fn=lambda r, **k: 1)
    assert res["reassigned"] == ["k0"] and len(res["spawned"]) == 1


def test_should_stop_conditions(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    run = {"state": "running", "lead_heartbeat_ts": 100.0}
    drn = {"state": "draining", "lead_heartbeat_ts": 100.0}
    assert teamcli._should_stop(run, in_flight=3, elapsed=10, lead_ttl=300, max_wall=1000, now=110) is None
    assert teamcli._should_stop(drn, in_flight=0, elapsed=10, lead_ttl=300, max_wall=1000, now=110) == "drained"
    assert teamcli._should_stop(drn, in_flight=2, elapsed=10, lead_ttl=300, max_wall=1000, now=110) is None
    assert teamcli._should_stop(run, in_flight=3, elapsed=10, lead_ttl=300, max_wall=1000, now=500) == "lead-heartbeat-stale"
    assert teamcli._should_stop(run, in_flight=3, elapsed=2000, lead_ttl=300, max_wall=1000, now=110) == "max-wall"


def test_pool_set_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / "t"
    rc, out = _call(capsys, "pool-set", "--root", str(root), "--width", "6", "--state", "running")
    doc = json.loads(out)
    assert rc == 0
    assert doc["width"] == 6 and doc["state"] == "running" and doc["lead_heartbeat_ts"] > 0


def test_coordinate_once_fills_to_width(tmp_path: Path, capsys) -> None:
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text("".join(
        json.dumps({"task_id": f"k{i}", "objective": "opt", "owns_paths": [f"k{i}/**"]}) + "\n"
        for i in range(3)), encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t", "--tasks", str(tasks))
    # one tick, width 2, stub teammate that exits immediately (no real codex)
    rc, out = _call(capsys, "coordinate", "--root", str(root), "--team-id", "t",
                    "--cwd", str(tmp_path), "--width", "2", "--once", "--exec-cmd", "true")
    res = json.loads(out)
    assert rc == 0 and res["stopped"] == "once" and len(res["spawned"]) == 2
    assert sorted(s["member_id"] for s in res["spawned"]) == ["w1", "w2"]
    assert tb.count_in_flight(root) == 2          # the 2 claimed tasks occupy the pool


def test_coordinate_draining_does_not_spawn(tmp_path: Path, capsys) -> None:
    root = tmp_path / "t"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps({"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]}) + "\n",
                     encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t", "--tasks", str(tasks))
    _call(capsys, "pool-set", "--root", str(root), "--state", "draining")
    # draining + nothing in flight -> stop immediately, spawn nothing
    rc, out = _call(capsys, "coordinate", "--root", str(root), "--team-id", "t",
                    "--cwd", str(tmp_path), "--width", "4", "--exec-cmd", "true")
    res = json.loads(out)
    assert rc == 0 and res["stopped"] == "drained"
