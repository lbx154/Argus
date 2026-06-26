from __future__ import annotations

from pathlib import Path

from argus_skill.team import pool


def test_read_default_when_missing(tmp_path: Path) -> None:
    # Slim control file: just {width?, state}. No lead heartbeat — the resident
    # Curator replaces the M2 orphan-protection heartbeat. ``width`` is absent
    # until explicitly set (absent != 0).
    assert pool.read(tmp_path) == {"state": "running"}


def test_update_merges_without_heartbeat(tmp_path: Path) -> None:
    pool.update(tmp_path, width=8, state="running")
    assert pool.read(tmp_path) == {"width": 8, "state": "running"}
    # partial update keeps width, flips state; never stamps a heartbeat
    pool.update(tmp_path, state="draining")
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "draining"
    assert "lead_heartbeat_ts" not in p


def test_width_zero_is_explicit_pause_not_unset(tmp_path: Path) -> None:
    # BUG-2: width=0 must mean PAUSE (target 0 in-flight), distinguishable from
    # "never set" (which falls back to the Curator's default width).
    assert "width" not in pool.read(tmp_path)
    pool.update(tmp_path, width=0, state="running")
    assert pool.read(tmp_path)["width"] == 0
