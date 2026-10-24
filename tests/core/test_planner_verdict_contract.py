from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.planner_verdict import (
    adapt_legacy_planner_verdict_event,
    build_planner_verdict_event,
)
from argus.life.memory import EventJournal


def test_planner_verdict_builder_requires_explicit_status() -> None:
    with pytest.raises(TypeError):
        build_planner_verdict_event(reason="missing status")  # type: ignore[call-arg]


def test_doctoral_novelty_unverified_maps_to_research_incomplete() -> None:
    """Req 10: doctoral + novelty_unverified → research_incomplete, success=false."""
    adapted = adapt_legacy_planner_verdict_event({
        "type": "life.planner.verdict",
        "project_done": True,
        "reason": "doctoral target with novelty_unverified",
        "research_target_level": "doctoral",
        "math_result": {
            "result_class": "novelty_unverified",
            "correctness": "verified",
            "novelty": "unverified",
            "statement_fidelity": "verified",
            "evidence": ["checked finite cases"],
            "limitations": ["novelty not established"],
        },
    })

    assert adapted["status"] == "research_incomplete"
    assert adapted["success"] is False
    assert adapted["novelty_status"] == "unverified"
    assert adapted["correctness_status"] == "verified"
    assert adapted["significance_status"] not in {"publishable", "doctoral"}


def test_budget_exhausted_legacy_maps_to_paused_budget() -> None:
    """Req 11: budget_exhausted stop_kind → paused_budget status."""
    adapted = adapt_legacy_planner_verdict_event({
        "type": "life.planner.verdict",
        "project_done": False,
        "reason": "budget limit reached",
        "stop_kind": "budget_exhausted",
    })

    assert adapted["status"] == "paused_budget"
    assert adapted["success"] is False
    assert adapted["recoverable"] is True


def test_legacy_generic_project_done_maps_to_completed() -> None:
    adapted = adapt_legacy_planner_verdict_event({
        "type": "life.planner.verdict",
        "project_done": True,
        "reason": "legacy generic project completed",
    })

    assert adapted["status"] == "completed"
    assert adapted["success"] is True


def test_event_journal_applies_generic_legacy_verdict_adapter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "life.planner.verdict",
            "project_done": False,
            "reason": "legacy verdict without a status",
            "task_count": 0,
        })
        + "\n",
        encoding="utf-8",
    )

    entry = EventJournal(path).all()[0]

    assert entry.extra["status"] == "research_incomplete"
    assert entry.extra["success"] is False
    assert "event_validation" not in entry.extra


def test_production_producers_do_not_handwrite_planner_verdict_payloads() -> None:
    root = Path(__file__).parents[2] / "argus" / "life" / "supervisor"
    for relative in ("_planning_cycle.py", "_planning_context.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert '"type": EventType.LIFE_PLANNER_VERDICT' not in source
