"""Cut #3: semantic stall kill-switch in the supervised round classifier.

Verifies that ``SupervisedEngineer._classify`` only bails as ``no_progress``
on an EXPLICIT, sustained ``forward_progress == false`` signal — never on a
missing field, never below threshold, and never on the final round (where
``max_rounds`` must win the terminal label).
"""
from __future__ import annotations

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import SupervisedEngineer


def _review(status: str, *, forward_progress=None) -> ReviewDecision:
    planner_report: dict = {}
    if forward_progress is not None:
        planner_report["forward_progress"] = forward_progress
    return ReviewDecision(
        status=status,
        reason="r",
        next_action="keep going",
        planner_report=planner_report,
    )


def _classify(streak: int, threshold: int, *, round_index: int = 5,
              max_rounds: int = 500):
    return SupervisedEngineer._classify(
        review=_review("continue"),
        checks_results=[],
        no_progress_streak=0,
        no_progress_threshold=2,
        semantic_stall_streak=streak,
        stall_threshold=threshold,
        round_index=round_index,
        max_rounds=max_rounds,
    )


def test_stall_kills_at_threshold() -> None:
    status, reason = _classify(streak=8, threshold=8)
    assert status == "no_progress"
    assert "forward progress" in reason


def test_stall_below_threshold_continues() -> None:
    status, _ = _classify(streak=7, threshold=8)
    assert status is None


def test_stall_disabled_when_threshold_zero() -> None:
    status, _ = _classify(streak=100, threshold=0)
    assert status is None


def test_max_rounds_wins_on_final_round() -> None:
    # On the very last round, the loop's own max_rounds fallthrough must win
    # the terminal label instead of a stall kill.
    status, _ = _classify(streak=50, threshold=8, round_index=500, max_rounds=500)
    assert status is None
