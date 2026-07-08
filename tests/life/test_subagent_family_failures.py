"""Unit tests for the subagent family failure-streak circuit breaker.

Regression coverage for the observed pathology: a continuous-mode daemon's L4
planner re-queued "fix the SWE-bench full-canary run" ~20 times over 2 days
(each time worded differently), because the exact-text duplicate/recent-
failure dedup in ``life/supervisor/_core.py`` never saw the actual repeated
failure — it lived in the subagent registry (``.argus_subagents/*.json``),
one layer below the journal-level mission bookkeeping the dedup inspects.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill.life.supervisor._subagent_family_failures import (
    family_from_task_id,
    recent_subagent_family_failures,
)


# ---------------------------------------------------------------------------
# family_from_task_id
# ---------------------------------------------------------------------------


def test_family_from_task_id_strips_trailing_timestamp() -> None:
    assert (
        family_from_task_id("swebench-verified-full-canary-20260706T123839Z")
        == "swebench-verified-full-canary"
    )


def test_family_from_task_id_handles_no_timestamp_suffix() -> None:
    assert family_from_task_id("visual_expanded_multisource_70") == (
        "visual_expanded_multisource_70"
    )


def test_family_from_task_id_empty_string() -> None:
    assert family_from_task_id("") == ""


# ---------------------------------------------------------------------------
# recent_subagent_family_failures — registry I/O + streak logic
# ---------------------------------------------------------------------------


def _write_record(registry_dir: Path, task_id: str, state: str, *, started_at: float,
                   **extra: object) -> None:
    registry_dir.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "task_id": task_id, "started_at": started_at, **extra}
    (registry_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_registry_returns_empty(tmp_path: Path) -> None:
    assert recent_subagent_family_failures(tmp_path) == {}


def test_empty_registry_dir_returns_empty(tmp_path: Path) -> None:
    (tmp_path / ".argus_subagents").mkdir()
    assert recent_subagent_family_failures(tmp_path) == {}


def test_consecutive_errors_trip_the_breaker(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    for i in range(5):
        _write_record(
            registry, f"swebench-verified-full-canary-2026070{i}T000000Z",
            "error", started_at=now - i * 3600, stop_reason="git_apply_check_failed",
        )
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert "swebench-verified-full-canary" in result
    failure = result["swebench-verified-full-canary"]
    assert failure.streak == 5
    assert failure.last_state == "error"
    assert failure.last_reason == "git_apply_check_failed"


def test_below_threshold_streak_is_not_reported(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    for i in range(2):
        _write_record(
            registry, f"dbbench-2026070{i}T000000Z", "error", started_at=now - i * 3600,
        )
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert result == {}


def test_older_success_does_not_break_a_newer_streak(tmp_path: Path) -> None:
    """A `done` OLDER than the failure run must not cancel the current streak."""
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    _write_record(registry, "fam-20260101T000000Z", "done", started_at=now - 30 * 3600)
    for i in range(4):
        _write_record(registry, f"fam-2026070{i}T000000Z", "error", started_at=now - i * 3600)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert result["fam"].streak == 4


def test_recent_success_resets_the_streak(tmp_path: Path) -> None:
    """A `done` MORE RECENT than the failures clears the streak (recovered)."""
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    for i in range(1, 6):
        _write_record(registry, f"fam-2026070{i}T000000Z", "error", started_at=now - i * 3600)
    _write_record(registry, "fam-20260107T000000Z", "done", started_at=now)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert "fam" not in result


def test_early_stopped_counts_as_a_failure(tmp_path: Path) -> None:
    """early_stopped means the subagent's OWN supervisor intervened on a
    degrading/stuck/diverging run — that attempt did not succeed either."""
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    for i in range(3):
        _write_record(registry, f"fam-2026070{i}T000000Z", "early_stopped", started_at=now - i * 3600)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert result["fam"].streak == 3
    assert result["fam"].last_state == "early_stopped"


def test_in_flight_states_are_excluded_from_the_streak(tmp_path: Path) -> None:
    """A currently-running/preflight/discussing record has not concluded yet;
    it must not count as either a success or a failure."""
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    _write_record(registry, "fam-20260105T000000Z", "running", started_at=now)
    for i in range(1, 4):
        _write_record(registry, f"fam-2026070{i}T000000Z", "error", started_at=now - i * 3600)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert result["fam"].streak == 3


def test_window_seconds_excludes_stale_attempts(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    # Only the freshest attempt (age 0) is inside a 30-minute window.
    for i in range(5):
        _write_record(registry, f"fam-2026070{i}T000000Z", "error", started_at=now - i * 3600)
    result = recent_subagent_family_failures(
        tmp_path, now=now, window_seconds=1800, min_streak=1,
    )
    assert result["fam"].streak == 1


def test_distinct_families_tracked_independently(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    for i in range(3):
        _write_record(registry, f"swebench-verified-full-canary-2026070{i}T000000Z",
                      "error", started_at=now - i * 3600)
    for i in range(3):
        _write_record(registry, f"dbbench-api-full-matrix-2026070{i}T010000Z",
                      "error", started_at=now - i * 3600)
    # One healthy family should never show up.
    _write_record(registry, "tau2-official-airline-smoke-20260706T130100Z", "done", started_at=now)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert set(result) == {"swebench-verified-full-canary", "dbbench-api-full-matrix"}


def test_malformed_json_file_is_skipped_fail_soft(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir(parents=True)
    (registry / "broken.json").write_text("{not json", encoding="utf-8")
    now = time.time()
    for i in range(3):
        _write_record(registry, f"fam-2026070{i}T000000Z", "error", started_at=now - i * 3600)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=3)
    assert result["fam"].streak == 3


def test_min_streak_zero_or_negative_still_requires_at_least_one(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    now = time.time()
    _write_record(registry, "fam-20260701T000000Z", "error", started_at=now)
    result = recent_subagent_family_failures(tmp_path, now=now, min_streak=0)
    assert result["fam"].streak == 1
