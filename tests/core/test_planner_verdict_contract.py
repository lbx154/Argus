from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.event_catalog import validate_event_envelope
from argus_skill.core.planner_verdict import (
    PlannerVerdictStatus,
    adapt_legacy_planner_verdict_event,
    build_planner_verdict_event,
)
from argus_skill.life.memory import EventJournal


def test_planner_verdict_builder_produces_complete_valid_event() -> None:
    event = build_planner_verdict_event(
        status=PlannerVerdictStatus.COMPLETED,
        reason="reviewer certified the persisted completion contract",
        project_id="project-1",
        mission_id="mission-1",
        research_target_level="doctoral",
        correctness_status="verified",
        novelty_status="verified_new",
        significance_status="doctoral",
        completion_kind="project_completed",
        project_done=True,
        enqueued_tasks=0,
    )

    assert event["type"] == "life.planner.verdict"
    assert event["status"] == "completed"
    assert event["success"] is True
    assert event["recoverable"] is False
    assert event["summary"] == event["reason"]
    assert event["stop_kind"] is None
    assert validate_event_envelope(event, require_known=True).valid is True


@pytest.mark.parametrize(
    ("status", "success", "recoverable"),
    [
        (PlannerVerdictStatus.COMPLETED, True, False),
        (PlannerVerdictStatus.RESEARCH_INCOMPLETE, False, True),
        (PlannerVerdictStatus.PAUSED_BUDGET, False, True),
        (PlannerVerdictStatus.PAUSED_NO_BREAKTHROUGH, False, True),
        (PlannerVerdictStatus.EXHAUSTED_CURRENT_METHODS, False, True),
        (PlannerVerdictStatus.PROVIDER_COOLDOWN, False, True),
        (PlannerVerdictStatus.PROVIDER_FENCE, False, True),
        (PlannerVerdictStatus.INFRA_BLOCKED, False, True),
        (PlannerVerdictStatus.ERROR, False, False),
    ],
)
def test_planner_verdict_status_policy_is_centralized(
    status: PlannerVerdictStatus,
    success: bool,
    recoverable: bool,
) -> None:
    event = build_planner_verdict_event(status=status, reason=status.value)

    assert event["success"] is success
    assert event["recoverable"] is recoverable
    assert validate_event_envelope(event, require_known=True).valid is True


def test_planner_verdict_builder_requires_explicit_status() -> None:
    with pytest.raises(TypeError):
        build_planner_verdict_event(reason="missing status")  # type: ignore[call-arg]


def test_legacy_unverified_research_verdict_maps_conservatively() -> None:
    adapted = adapt_legacy_planner_verdict_event({
        "type": "life.planner.verdict",
        "project_done": True,
        "reason": "legacy Math review ended",
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
    assert adapted["research_target_level"] is None
    assert adapted["correctness_status"] == "verified"
    assert adapted["novelty_status"] == "unverified"
    assert adapted["significance_status"] == "exploratory"
    assert adapted["novelty_status"] != "verified_new"
    assert adapted["significance_status"] not in {"publishable", "doctoral"}


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
    root = Path(__file__).parents[2] / "argus_skill" / "life" / "supervisor"
    for relative in ("_planning_cycle.py", "_planning_context.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert '"type": EventType.LIFE_PLANNER_VERDICT' not in source
