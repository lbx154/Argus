"""Regression test: re-affirming a research target must not retire evidence.

``research_target_set_at`` is the cutoff ``_research_project_done_issue`` uses
to retire certifications earned against an *earlier, different* target. It was
stamped on every ``persist_vertical`` call that carried a level, including the
overwhelmingly common case of a caller re-persisting the level it had just
read. Each re-stamp moved the cutoff past every journal entry, so the gate
loop — which walks newest-first and breaks at the first mission older than the
cutoff — could never find a certification and rejected Planner completion
forever.

Observed live in run 8 (s-fed750c2): the universal-moduli problem was solved
and formalized in Lean in mission 1, and missions 2, 3 and 4 were all attempts
to certify that same finished work, each independently reviewed ``done`` and
each answered with ``missing_exploratory_reviewer_certification``.

Citations:
- argus_skill/skills/vertical_select.py — ``persist_vertical``
- argus_skill/life/supervisor/_planning_cycle_helpers.py
  — ``_research_project_done_issue``
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.supervisor._planning_cycle_helpers import (
    _research_project_done_issue,
)
from argus_skill.skills.vertical_select import _state_path, persist_vertical

TARGET = "exploratory"


def _stamp(root: Path) -> float:
    payload = json.loads(_state_path(root).read_text(encoding="utf-8"))
    return float(payload["research_target_set_at"])


def test_re_persisting_the_same_target_does_not_move_the_cutoff(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math", research_target_level=TARGET)
    first = _stamp(tmp_path)

    for _ in range(3):
        persist_vertical(tmp_path, "math", research_target_level=TARGET)

    assert _stamp(tmp_path) == first, (
        "re-affirming the level already persisted moved the certification "
        "cutoff; every certification recorded before this call is now "
        "retroactively too old to satisfy the completion gate"
    )


def test_raising_the_target_does_move_the_cutoff(tmp_path: Path) -> None:
    """The cutoff still does its actual job."""
    persist_vertical(tmp_path, "math", research_target_level=TARGET)
    first = _stamp(tmp_path)

    persist_vertical(tmp_path, "math", research_target_level="publishable")

    assert _stamp(tmp_path) > first, (
        "a genuinely different target must retire evidence earned against the "
        "old one"
    )


def test_research_direction_cannot_change_without_new_intent(tmp_path: Path) -> None:
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
        research_direction_mode="broad",
    )

    with pytest.raises(ValueError, match="cannot change"):
        persist_vertical(
            tmp_path,
            "research",
            research_target_level="publishable",
            research_direction_mode="locked",
        )


def test_new_intent_can_replace_research_direction(tmp_path: Path) -> None:
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
        research_direction_mode="broad",
    )

    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
        research_direction_mode="locked",
        allow_research_direction_change=True,
    )

    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["research_direction_mode"] == "locked"


def test_a_certified_mission_survives_later_planning_cycles(
    tmp_path: Path,
) -> None:
    """The end-to-end livelock, at the gate that rejected run 8 four times."""
    persist_vertical(tmp_path, "math", research_target_level=TARGET)

    # Certified against the target as it stands right now — the earliest
    # moment the gate accepts, and so the one a re-stamp invalidates first.
    certification = SimpleNamespace(
        kind="mission_complete",
        ts=_stamp(tmp_path),
        extra={"scope": "final_submission", "final_submission_certified": True},
    )
    assert _research_project_done_issue(tmp_path, [certification]) == ""

    # A later cycle re-resolves the vertical and re-affirms the same target.
    time.sleep(0.01)
    persist_vertical(tmp_path, "math", research_target_level=TARGET)

    assert _research_project_done_issue(tmp_path, [certification]) == "", (
        "the certification was valid, then a re-resolve of the unchanged "
        "vertical invalidated it — the Planner can never satisfy this gate"
    )


@pytest.mark.parametrize("missing", ["absent", "blank"])
def test_a_missing_stamp_is_backfilled(tmp_path: Path, missing: str) -> None:
    """State written before this field existed still gets a cutoff."""
    persist_vertical(tmp_path, "math", research_target_level=TARGET)
    path = _state_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if missing == "absent":
        payload.pop("research_target_set_at")
    else:
        payload["research_target_set_at"] = 0.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    persist_vertical(tmp_path, "math", research_target_level=TARGET)

    assert _stamp(tmp_path) > 0.0
