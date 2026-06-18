from __future__ import annotations

from pathlib import Path

from argus_skill.team import pool


def test_read_default_when_missing(tmp_path: Path) -> None:
    assert pool.read(tmp_path) == {"width": 0, "state": "running", "lead_heartbeat_ts": 0.0}


def test_update_merges_and_stamps_heartbeat(tmp_path: Path) -> None:
    pool.update(tmp_path, width=8, state="running", now=10.0)
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "running" and p["lead_heartbeat_ts"] == 10.0
    # partial update keeps width, flips state, refreshes heartbeat
    pool.update(tmp_path, state="draining", now=20.0)
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "draining" and p["lead_heartbeat_ts"] == 20.0
