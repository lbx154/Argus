from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.life.supervisor._planning_cycle_helpers import (
    _research_project_done_issue,
)
from argus_skill.skills.vertical_select import persist_vertical


def test_same_target_reclassification_does_not_invalidate_completed_review(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter((100.0, 300.0))
    monkeypatch.setattr(
        "argus_skill.skills.vertical_select.time.time",
        lambda: next(clock),
    )
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="exploratory",
    )
    certification = SimpleNamespace(
        kind="mission_complete",
        ts=200.0,
        extra={
            "scope": "final_submission",
            "final_submission_certified": True,
        },
    )

    # The Manager's normal post-mission reclassification reasserts the same
    # target. It must not open a new evidence epoch after the certification.
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="exploratory",
    )

    assert _research_project_done_issue(tmp_path, [certification]) == ""
