from __future__ import annotations

from pathlib import Path

from argus_skill.team import roster as rs


def test_create_and_add_member(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="optimize kernels", lead="lead", now=1.0)
    rs.add_member(tmp_path, {"id": "tm-1", "pid": 111, "cwd": "project/a",
                             "task_id": "a", "status": "running", "heartbeat_ts": 1.0})
    doc = rs.load(tmp_path)
    assert doc["team_id"] == "t1" and doc["lead"] == "lead"
    assert [m["id"] for m in rs.members(tmp_path)] == ["tm-1"]


def test_add_member_replaces_same_id(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="m", lead="lead", now=1.0)
    rs.add_member(tmp_path, {"id": "tm-1", "status": "running", "heartbeat_ts": 1.0})
    rs.add_member(tmp_path, {"id": "tm-1", "status": "running", "heartbeat_ts": 2.0, "pid": 9})
    assert len(rs.members(tmp_path)) == 1
    assert rs.members(tmp_path)[0]["pid"] == 9


def test_set_member_status_preserves_process_metadata(tmp_path: Path) -> None:
    rs.add_member(tmp_path, {
        "id": "w1",
        "pid": 42,
        "cwd": "/project",
        "task_id": "t::a",
        "status": "running",
    })
    rs.set_member_status(tmp_path, "w1", "exited")
    assert rs.members(tmp_path) == [{
        "id": "w1",
        "pid": 42,
        "cwd": "/project",
        "task_id": "t::a",
        "status": "exited",
    }]


def test_next_member_id_monotonic_unique(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="m", lead="lead", now=1.0)
    ids = [rs.next_member_id(tmp_path, prefix="w") for _ in range(3)]
    assert ids == ["w1", "w2", "w3"]                 # monotonic, unique
    # works even without create() (fresh roster)
    assert rs.next_member_id(tmp_path / "fresh", prefix="k") == "k1"
