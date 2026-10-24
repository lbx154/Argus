"""Regression test: re-affirming a research target must not retire evidence.

``research_target_set_at`` is the cutoff ``_research_project_done_issue`` uses
to retire certifications earned against an earlier target, vertical, or intent. It was
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
- argus/skills/vertical_select.py — ``persist_vertical``
- argus/life/supervisor/_planning_cycle_helpers.py
  — ``_research_project_done_issue``
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life.supervisor._planning_cycle_helpers import (
    _research_project_done_issue,
)
from argus.skills.vertical_select import _state_path, persist_vertical

TARGET = "exploratory"

pytestmark = pytest.mark.usefixtures("targeted_vertical")


def _stamp(root: Path) -> float:
    payload = json.loads(_state_path(root).read_text(encoding="utf-8"))
    return float(payload["research_target_set_at"])


@pytest.mark.parametrize("target_level", [TARGET, None])
def test_changing_vertical_retires_same_target_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_level: str | None,
) -> None:
    monkeypatch.setattr("argus.skills.vertical_select.time.time", lambda: 100.0)
    persist_vertical(tmp_path, "research", research_target_level=TARGET)
    certification = SimpleNamespace(
        kind="mission_complete",
        ts=200.0,
        extra={"scope": "final_submission", "final_submission_certified": True},
    )
    assert _research_project_done_issue(tmp_path, [certification]) == ""

    monkeypatch.setattr("argus.skills.vertical_select.time.time", lambda: 300.0)
    persist_vertical(tmp_path, "targeted_lab", research_target_level=target_level)

    assert _research_project_done_issue(
        tmp_path, [certification]
    ) == "missing_exploratory_reviewer_certification"
    assert _stamp(tmp_path) == 300.0


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
