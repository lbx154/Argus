"""paper_mission must follow the VERTICAL, not a True default.

Regression: a kernel-grind objective ("research SOL-ExecBench, grind 2 kernels")
correctly routed to the kernelbench vertical, but paper_mission stayed True
(coarse default), so the supervisor picked the research run-stage pilot gate and
dumped a PILOT_OPERATOR_DECISION_TEMPLATE.json — a $0.55 blocked no-op. The fix
(apps/_runtime.py + loop.py) derives paper_mission from the vertical's completion
gate: only ``full_paper`` verticals are paper missions. This pins that invariant.
"""
from __future__ import annotations

import pytest

from argus_skill.apps._runtime import _paper_mission_for_project_root
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_completion_gate

OPTIMIZE = ["kernelbench", "speedrun", "nanochat", "nanogpt_speedrun"]


@pytest.mark.parametrize("vertical", OPTIMIZE)
def test_optimize_verticals_are_not_paper(vertical: str) -> None:
    assert vertical_completion_gate(load_vertical(vertical)) != "full_paper"


def test_research_is_paper() -> None:
    assert vertical_completion_gate(load_vertical("research")) == "full_paper"


def test_undecided_project_is_not_implicitly_paper(tmp_path) -> None:
    assert _paper_mission_for_project_root(tmp_path) is False


def test_persisted_research_project_is_paper(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    assert _paper_mission_for_project_root(tmp_path) is True


def test_persisted_bounded_vertical_is_not_paper(tmp_path) -> None:
    persist_vertical(tmp_path, "kernelbench")
    assert _paper_mission_for_project_root(tmp_path) is False
