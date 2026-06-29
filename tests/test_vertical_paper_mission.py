"""paper_mission must follow the VERTICAL, not a True default.

Regression: a kernel-grind objective ("research SOL-ExecBench, grind 2 kernels")
correctly routed to the kernelbench vertical, but paper_mission stayed True
(coarse default), so the supervisor picked the research run-stage pilot gate and
dumped a PILOT_OPERATOR_DECISION_TEMPLATE.json — a $0.55 blocked no-op. The fix
(apps/_runtime.py + loop.py) derives paper_mission from the vertical's completion
gate: only ``full_emnlp`` verticals are paper missions. This pins that invariant.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals._base import load_vertical, vertical_completion_gate

OPTIMIZE = ["kernelbench", "speedrun", "nanochat", "nanogpt_speedrun"]


@pytest.mark.parametrize("vertical", OPTIMIZE)
def test_optimize_verticals_are_not_paper(vertical: str) -> None:
    assert vertical_completion_gate(load_vertical(vertical)) != "full_emnlp"


def test_research_is_paper() -> None:
    assert vertical_completion_gate(load_vertical("research")) == "full_emnlp"
