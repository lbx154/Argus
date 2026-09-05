"""Planner no-progress quarantine survival: settlement window, age, release.

The quarantine used to scan the last 20 journal entries of ANY kind, so a
talkative planner (waiting heartbeats, verdict cycles) washed real failures
out of the window while a quiet journal quarantined a signature forever.
Survival is now bounded by qualifying mission settlements only — window slots
hold mission_failed/mission_complete; paused_* and iteration-requeue
settlements neither occupy slots nor count as releasing successes — plus a
wall-clock maximum age and a completed-mission release count.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._constants import (
    planner_quarantine_max_age_hours,
    planner_quarantine_release_successes,
    planner_quarantine_settlement_window,
)
from argus_skill.life.supervisor._planner_orchestration import (
    PlannerOrchestrationMixin,
)


class _QuarantineHost(PlannerOrchestrationMixin):
    """Minimal host: the quarantine reads only ``self.memory.journal``."""

    def __init__(self, memory: LifeMemory) -> None:
        self.memory = memory


def _open_host(tmp_path: Path) -> tuple[_QuarantineHost, Path]:
    mem = LifeMemory.open(tmp_path / "life")
    return _QuarantineHost(mem), mem.journal.path


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _no_progress_failure(path: Path, *, title: str, ts: float) -> None:
    _append(path, {
        "type": "life.mission.completed",
        "item_id": "feedfacecafe",
        "ts": ts,
        "success": False,
        "status": "no_progress",
        "terminal_status": "no_progress",
        "title": title,
        "objective": f"objective for {title}",
        "summary": "stalled with no forward progress",
    })


def _success(path: Path, *, ts: float, requeued: bool = False) -> None:
    row: dict[str, Any] = {
        "type": "life.mission.completed",
        "item_id": "0badc0de0000",
        "ts": ts,
        "success": True,
        "status": "done",
        "title": "productive mission",
        "summary": "done",
    }
    if requeued:
        row["iteration"] = {"requeued": True}
    _append(path, row)


def _neutral_settlement(path: Path, *, ts: float) -> None:
    """A settlement that is neither a quarantine hit nor a release success."""
    _append(path, {
        "type": "life.mission.completed",
        "item_id": "0badc0de0001",
        "ts": ts,
        "success": False,
        "status": "paused_budget",
        "title": "paused mission",
        "summary": "budget pause",
    })


def _planner_noise(path: Path, *, ts: float) -> None:
    _append(path, {
        "type": "life.planner.waiting",
        "ts": ts,
        "reason": "waiting on external dependency",
    })


def _quarantined_titles(host: _QuarantineHost) -> set[str]:
    return {
        entry.title for entry in host._recent_no_progress_failures().values()
    }


def test_planner_chatter_does_not_dilute_quarantine(tmp_path: Path) -> None:
    """30 planner_waiting heartbeats after 2 failures must not release them.

    Regression: the old 20-entry any-kind tail was fully occupied by the
    noise, so both failures dropped out of quarantine.
    """
    host, path = _open_host(tmp_path)
    now = time.time()
    _no_progress_failure(path, title="doomed alpha", ts=now - 600.0)
    _no_progress_failure(path, title="doomed beta", ts=now - 500.0)
    for index in range(30):
        _planner_noise(path, ts=now - 400.0 + index)

    assert _quarantined_titles(host) == {"doomed alpha", "doomed beta"}


def test_quarantine_expires_after_max_age(tmp_path: Path) -> None:
    host, path = _open_host(tmp_path)
    now = time.time()
    expired_ts = now - (planner_quarantine_max_age_hours() + 1.0) * 3600.0
    _no_progress_failure(path, title="ancient failure", ts=expired_ts)
    _no_progress_failure(path, title="fresh failure", ts=now - 3600.0)

    assert _quarantined_titles(host) == {"fresh failure"}


def test_successful_settlements_release_quarantine(tmp_path: Path) -> None:
    host, path = _open_host(tmp_path)
    now = time.time()
    release_after = planner_quarantine_release_successes()
    assert release_after == 3

    # Successes OLDER than the failure prove nothing about it.
    _success(path, ts=now - 900.0)
    _success(path, ts=now - 890.0)
    _no_progress_failure(path, title="doomed task", ts=now - 800.0)
    # Two newer completions are not enough...
    _success(path, ts=now - 700.0)
    _success(path, ts=now - 650.0)
    # ...an iteration requeue is a re-plan, not a completion...
    _success(path, ts=now - 600.0, requeued=True)
    # ...and a neutral settlement (budget pause) never counts as one.
    _neutral_settlement(path, ts=now - 500.0)
    assert _quarantined_titles(host) == {"doomed task"}

    # The third completed mission after the failure releases it.
    _success(path, ts=now - 400.0)
    assert _quarantined_titles(host) == set()


def test_iterated_settlements_do_not_release_quarantine(tmp_path: Path) -> None:
    """Requeues are re-plans, not progress (red before the fix).

    Production run s-3e28f79c released a no_progress signature 48 minutes
    after the failure purely on the strength of ``mission_iterated``
    settlements — every one of them the same task being put back in the
    queue. Any number of requeues must leave the quarantine standing.
    """
    host, path = _open_host(tmp_path)
    now = time.time()
    _no_progress_failure(path, title="doomed task", ts=now - 3600.0)
    for index in range(planner_quarantine_release_successes() * 3):
        _success(path, ts=now - 1800.0 + index, requeued=True)

    assert _quarantined_titles(host) == {"doomed task"}


def test_pause_noise_does_not_occupy_window_slots(tmp_path: Path) -> None:
    """paused_* settlements must not evict a failure out of the window.

    Production journals show one pause settlement per hour (s-3e28f79c), so a
    20-slot window of ALL settlements forgot a failure within a day even when
    nothing completed. Pauses and requeues no longer hold window slots.
    """
    host, path = _open_host(tmp_path)
    now = time.time()
    _no_progress_failure(path, title="doomed task", ts=now - 7200.0)
    for index in range(planner_quarantine_settlement_window() + 10):
        _neutral_settlement(path, ts=now - 3600.0 + index)
        _success(path, ts=now - 3500.0 + index, requeued=True)

    assert _quarantined_titles(host) == {"doomed task"}


def test_env_overrides_bound_quarantine_survival(
    tmp_path: Path, monkeypatch
) -> None:
    now = time.time()

    monkeypatch.setenv("ARGUS_SKILL_PLANNER_QUARANTINE_MAX_AGE_HOURS", "1")
    assert planner_quarantine_max_age_hours() == 1.0
    host, path = _open_host(tmp_path)
    _no_progress_failure(path, title="two hours old", ts=now - 2.0 * 3600.0)
    assert _quarantined_titles(host) == set()
    monkeypatch.delenv("ARGUS_SKILL_PLANNER_QUARANTINE_MAX_AGE_HOURS")
    assert _quarantined_titles(host) == {"two hours old"}

    monkeypatch.setenv("ARGUS_SKILL_PLANNER_QUARANTINE_RELEASE_SUCCESSES", "1")
    assert planner_quarantine_release_successes() == 1
    _success(path, ts=now - 3600.0)
    assert _quarantined_titles(host) == set()

    # 0 disables the success-release path entirely.
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_QUARANTINE_RELEASE_SUCCESSES", "0")
    assert planner_quarantine_release_successes() == 0
    assert _quarantined_titles(host) == {"two hours old"}

    # A shrunken settlement window evicts the failure behind newer
    # slot-occupying settlements (completions; the release path stays
    # disabled by the RELEASE_SUCCESSES=0 override above).
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_QUARANTINE_SETTLEMENT_WINDOW", "2")
    assert planner_quarantine_settlement_window() == 2
    _success(path, ts=now - 1800.0)
    assert _quarantined_titles(host) == set()
    monkeypatch.delenv("ARGUS_SKILL_PLANNER_QUARANTINE_SETTLEMENT_WINDOW")
    assert _quarantined_titles(host) == {"two hours old"}
