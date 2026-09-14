"""paper_mission must follow the VERTICAL, not a True default.

Regression: paper behavior must be an explicit vertical capability
(``PAPER_MISSION``), not inferred from ``completion_gate="certified"``. A
reviewer-certified report vertical (the community ``quant`` / ``medical`` are
the live examples) must not inherit the research paper pipeline.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime import (
    _final_certification_for_project_root,
    _paper_mission_for_project_root,
)
from argus_skill.skills.stage_machine import ChecklistItem
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import (
    load_vertical,
    vertical_is_paper_mission,
)

OPTIMIZE = ["kernel_engineering", "math_synth"]


@pytest.mark.parametrize("vertical", OPTIMIZE)
def test_optimize_verticals_are_not_paper(vertical: str) -> None:
    assert vertical_is_paper_mission(load_vertical(vertical)) is False


def test_research_is_paper() -> None:
    assert vertical_is_paper_mission(load_vertical("research")) is True


def test_certified_gate_alone_does_not_make_a_paper_vertical() -> None:
    """A certified report vertical without ``PAPER_MISSION`` is not a paper mission."""
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("report",),
        CHECKLIST_ITEMS={"report": (ChecklistItem("report.done", "Report reviewed", "report"),)},
        completion_gate="certified",
    )

    assert vertical_is_paper_mission(provider) is False


def test_research_keeps_final_certification(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    assert _final_certification_for_project_root(tmp_path) is True


def test_undecided_project_is_not_implicitly_paper(tmp_path) -> None:
    assert _paper_mission_for_project_root(tmp_path) is False


def test_persisted_research_project_is_paper(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    assert _paper_mission_for_project_root(tmp_path) is True


def test_direct_research_project_is_not_a_paper_mission(tmp_path) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="direct")
    assert _paper_mission_for_project_root(tmp_path) is False


def test_persisted_bounded_vertical_is_not_paper(tmp_path) -> None:
    persist_vertical(tmp_path, "math_synth")
    assert _paper_mission_for_project_root(tmp_path) is False
