"""paper_mission must follow the VERTICAL, not a True default.

Regression: paper behavior must be an explicit vertical capability
(``PAPER_MISSION``), not inferred from ``completion_gate="certified"``. A
reviewer-certified report vertical (the community ``quant`` / ``medical`` are
the live examples) must not inherit the research paper pipeline.
"""
from __future__ import annotations

from argus.apps._runtime_supervisor import (
    _paper_mission_for_project_root,
)
from argus.skills.vertical_select import persist_vertical
from argus.verticals._base import load_vertical_contract


def test_optimize_verticals_are_not_paper(optimize_vertical: str) -> None:
    assert load_vertical_contract(optimize_vertical).paper_mission is False


def test_persisted_bounded_vertical_is_not_paper(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)
    assert _paper_mission_for_project_root(tmp_path) is False
