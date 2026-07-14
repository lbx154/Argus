from __future__ import annotations

from argus_skill.core.event_catalog import EventType
from argus_skill.core.models import ReviewDecision
from argus_skill.engineer import runner


def _review(plan_signal: str, reason: str = "") -> ReviewDecision:
    return ReviewDecision(
        status="continue",
        reason="review reason",
        next_action="continue locally",
        planner_report={
            "forward_progress": False,
            "headline": "route assessment",
            "blocker": "the current route no longer supports the objective",
            "recommended_next": "ask L4 to reconsider the remaining plan",
            "evidence_files": [
                {"path": "research/NO_GO.md", "why": "records the falsifier"},
            ],
            "plan_signal": plan_signal,
            "plan_signal_reason": reason,
        },
    )


def test_reconsider_signal_builds_shadow_event() -> None:
    event = runner._plan_signal_event(
        _review("reconsider", "new evidence invalidated the plan")
    )

    assert event == {
        "type": EventType.LIFE_PLAN_SIGNAL,
        "mode": "shadow",
        "signal": "reconsider",
        "reason": "new evidence invalidated the plan",
        "evidence_files": [
            {"path": "research/NO_GO.md", "why": "records the falsifier"},
        ],
    }


def test_continue_signal_does_not_emit_shadow_event() -> None:
    assert runner._plan_signal_event(_review("continue")) is None
