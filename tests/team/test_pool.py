from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.team import pool


def test_read_default_when_missing(tmp_path: Path) -> None:
    # Slim control file: just {width?, state}. No lead heartbeat — the resident
    # Curator replaces the M2 orphan-protection heartbeat. ``width`` is absent
    # until explicitly set (absent != 0).
    assert pool.read(tmp_path) == {"state": "running"}


def test_update_drops_retired_lead_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text(
        json.dumps({
            "width": 4,
            "state": "running",
            "lead_heartbeat_ts": 10.0,
        }),
        encoding="utf-8",
    )

    assert pool.read(tmp_path) == {"width": 4, "state": "running"}
    pool.update(tmp_path, width=8)
    assert pool.read(tmp_path) == {"width": 8, "state": "running"}
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "width": 8,
        "state": "running",
    }

    pool.update(tmp_path, state="draining")
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "draining"


def test_width_zero_is_explicit_pause_not_unset(tmp_path: Path) -> None:
    # BUG-2: width=0 must mean PAUSE (target 0 in-flight), distinguishable from
    # "never set" (which falls back to the Curator's default width).
    assert "width" not in pool.read(tmp_path)
    pool.update(tmp_path, width=0, state="running")
    assert pool.read(tmp_path)["width"] == 0


def test_width_and_state_are_safety_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_TEAM_MAX_WIDTH", "12")

    with pytest.raises(ValueError, match="exceeds ARGUS_TEAM_MAX_WIDTH"):
        pool.update(tmp_path, width=13)
    with pytest.raises(ValueError, match="non-negative"):
        pool.update(tmp_path, width=-1)
    with pytest.raises(ValueError, match="unsupported team pool state"):
        pool.update(tmp_path, state="exploding")



def test_default_width_leaves_one_provider_slot_for_the_lead(monkeypatch) -> None:
    """Two workers on a two-slot host starved the dispatching mission (2026-09-16)."""
    monkeypatch.setenv("ARGUS_TEAM_DEFAULT_WIDTH", "2")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "2")
    assert pool.default_width() == 1
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    assert pool.default_width() == 1  # never below one worker
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "8")
    assert pool.default_width() == 2  # the operator's width still wins when slots allow
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "0")
    assert pool.default_width() == 2  # no provider limit: unchanged behaviour
