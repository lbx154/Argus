from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.planner_verdict import build_planner_verdict_event


def test_planner_verdict_builder_requires_explicit_status() -> None:
    with pytest.raises(TypeError):
        build_planner_verdict_event(reason="missing status")  # type: ignore[call-arg]


def test_production_producers_do_not_handwrite_planner_verdict_payloads() -> None:
    root = Path(__file__).parents[2] / "argus" / "life" / "supervisor"
    for relative in ("_planning_cycle.py", "_planning_context.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert '"type": EventType.LIFE_PLANNER_VERDICT' not in source
