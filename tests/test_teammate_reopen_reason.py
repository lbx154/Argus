"""A reopened task tells its fresh worker why the finished artifact was rejected."""
from __future__ import annotations

from argus.team.teammate_entry import _with_reopen_reason


def test_reopen_reason_is_appended_to_the_objective() -> None:
    task = {"reason": "reopened: route file names no source"}
    rendered = _with_reopen_reason("Investigate route-01.", task)
    assert rendered.startswith("Investigate route-01.")
    assert "## Why this task was reopened" in rendered
    assert "route file names no source" in rendered


def test_fresh_tasks_keep_their_objective_unchanged() -> None:
    assert _with_reopen_reason("Investigate route-01.", {}) == "Investigate route-01."
    assert _with_reopen_reason("Investigate route-01.", {"reason": "  "}) == "Investigate route-01."
