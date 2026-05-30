"""Tests for the GPU lease helper (keep-alive coordination)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.tools import gpu_lease


def _isolate_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_GPU_LEASE_STATE_DIR", str(tmp_path / "state"))


# -- pure helpers -----------------------------------------------------------

def test_find_pids_in_matches_substring_and_excludes_self() -> None:
    cmdlines = [
        (10, "/opt/conda/bin/python /home/u/gpu_load.py --util 0.5"),
        (11, "python -c from multiprocessing.spawn import spawn_main"),
        (12, "python -m argus_skill.tools.gpu_lease claim"),
    ]
    assert gpu_lease.find_pids_in(cmdlines, "gpu_load.py", self_pid=99) == [10]
    assert gpu_lease.find_pids_in(cmdlines, "gpu_load.py", self_pid=10) == []


def test_collect_descendants_walks_the_tree() -> None:
    ppids = {200: 100, 201: 100, 300: 200, 400: 1}
    assert gpu_lease.collect_descendants([100], ppids) == [200, 201, 300]
    assert gpu_lease.collect_descendants([999], ppids) == []


def test_ancestors_walks_up_and_guards_cycles() -> None:
    assert gpu_lease._ancestors(5, {5: 4, 4: 3, 3: 1}) == {4, 3}
    assert gpu_lease._ancestors(7, {7: 8, 8: 7}) in ({8, 7}, {8})


def test_gpus_idle_thresholds() -> None:
    busy = [{"index": 0, "util_pct": 40, "mem_used_mib": 71529}]
    idle = [{"index": 0, "util_pct": 0, "mem_used_mib": 3}]
    assert gpu_lease.gpus_idle(busy) is False
    assert gpu_lease.gpus_idle(idle) is True
    assert gpu_lease.gpus_idle([]) is False  # no nvidia-smi -> not idle


def test_load_config_defaults_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_GPU_KEEPALIVE_CONFIG",
                       str(tmp_path / "nope.json"))
    cfg = gpu_lease.load_config()
    assert cfg["match"] == "gpu_load.py"
    assert isinstance(cfg["command"], list) and cfg["command"]


def test_load_config_reads_file_and_backfills(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "ka.json"
    path.write_text(json.dumps({
        "command": ["/p/python", "/p/gpu_load.py", "--util", "0.7"],
        "cwd": "/work", "match": "gpu_load.py",
    }), encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_GPU_KEEPALIVE_CONFIG", str(path))
    cfg = gpu_lease.load_config()
    assert cfg["command"][0] == "/p/python"
    assert cfg["cwd"] == "/work"
    assert cfg["log"]  # backfilled from defaults


# -- lease registry ---------------------------------------------------------

def test_active_leases_prunes_dead_pid(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: False)
    gpu_lease._write_lease("dead1", "agent", pid=424242, ttl=None, gpus="")
    assert gpu_lease.active_leases() == []
    assert not (gpu_lease._leases_dir() / "dead1.json").exists()


def test_active_leases_keeps_live_pid(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: True)
    gpu_lease._write_lease("live1", "agent", pid=12345, ttl=None, gpus="")
    assert [m["id"] for m in gpu_lease.active_leases()] == ["live1"]


def test_ttl_lease_expires(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    gpu_lease._write_lease("ttl1", "agent", pid=None, ttl=-1.0, gpus="")
    assert gpu_lease.active_leases() == []  # expires_at in the past


# -- park guards ------------------------------------------------------------

def test_park_refuses_while_lease_active(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: True)
    gpu_lease._write_lease("hold", "agent", pid=111, ttl=None, gpus="")

    def _boom(cfg):
        raise AssertionError("park must not start keep-alive over a lease")

    monkeypatch.setattr(gpu_lease, "_start_keepalive", _boom)
    res = gpu_lease.park({"match": "gpu_load.py"})
    assert res["refused"] is True
    assert "hold" in res["active_leases"]


def test_park_skips_zombie_match_and_starts(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "find_keepalive_pids", lambda match: [4242])
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: False)
    monkeypatch.setattr(gpu_lease, "_start_keepalive",
                        lambda cfg: {"started": True, "pid": 5555})
    res = gpu_lease.park({"match": "gpu_load.py"})
    assert res["started"] is True


def test_park_noop_when_alive(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "find_keepalive_pids", lambda match: [4242])
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: True)

    def _boom(cfg):
        raise AssertionError("park started keep-alive despite a live one")

    monkeypatch.setattr(gpu_lease, "_start_keepalive", _boom)
    res = gpu_lease.park({"match": "gpu_load.py"})
    assert res == {"started": False, "pid": None,
                   "already_running": True, "pids": [4242]}


# -- release semantics ------------------------------------------------------

def test_release_parks_only_when_last_lease(tmp_path: Path, monkeypatch) -> None:
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_lease, "_alive", lambda pid: True)
    monkeypatch.setattr(gpu_lease, "find_keepalive_pids", lambda match: [])
    parked = {"n": 0}

    def _fake_start(cfg):
        parked["n"] += 1
        return {"started": True, "pid": 999}

    monkeypatch.setattr(gpu_lease, "_start_keepalive", _fake_start)

    gpu_lease._write_lease("a", "m", pid=1, ttl=None, gpus="")
    gpu_lease._write_lease("b", "m", pid=2, ttl=None, gpus="")

    res1 = gpu_lease._release({"match": "gpu_load.py"}, "a")
    assert res1["parked"] is False and parked["n"] == 0
    res2 = gpu_lease._release({"match": "gpu_load.py"}, "b")
    assert res2["parked"] is True and parked["n"] == 1
