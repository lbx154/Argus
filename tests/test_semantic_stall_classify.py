"""Cut #3: semantic stall kill-switch in the supervised round classifier.

Verifies that ``SupervisedEngineer._classify`` only bails as ``no_progress``
on an EXPLICIT, sustained ``forward_progress == false`` signal — never on a
missing field, never below threshold, and never on the final round (where
``max_rounds`` must win the terminal label).
"""
from __future__ import annotations

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import SupervisedConfig, SupervisedEngineer


def _review(
    status: str,
    *,
    forward_progress=None,
    progress_class: str = "evidence",
) -> ReviewDecision:
    planner_report: dict = {}
    if forward_progress is not None:
        planner_report["forward_progress"] = forward_progress
    return ReviewDecision(
        status=status,
        reason="r",
        next_action="keep going",
        planner_report=planner_report,
        progress_class=progress_class,
    )


def _classify(streak: int, threshold: int, *, round_index: int = 5,
              max_rounds: int = 500, decision_idle_seconds: float = 0,
              decision_timeout_seconds: int = 0):
    return SupervisedEngineer._classify(
        review=_review("continue"),
        no_progress_streak=0,
        no_progress_threshold=2,
        semantic_stall_streak=streak,
        stall_threshold=threshold,
        round_index=round_index,
        max_rounds=max_rounds,
        decision_idle_seconds=decision_idle_seconds,
        decision_timeout_seconds=decision_timeout_seconds,
    )


def test_decision_progress_defaults_are_bounded() -> None:
    config = SupervisedConfig()

    assert config.stall_threshold == 2
    assert config.decision_progress_timeout_seconds == 1800


@pytest.mark.parametrize("progress_class", ["decision", "evidence"])
def test_decision_or_evidence_resets_stall_streak(progress_class: str) -> None:
    from argus_skill.engineer.runner import _next_decision_stall_streak

    assert _next_decision_stall_streak(
        _review("continue", progress_class=progress_class),
        1,
    ) == 0


@pytest.mark.parametrize(
    "progress_class",
    ["setup_only", "artifact_sync_only", "none"],
)
def test_nondecision_round_increments_stall_streak(progress_class: str) -> None:
    from argus_skill.engineer.runner import _next_decision_stall_streak

    assert _next_decision_stall_streak(
        _review("continue", progress_class=progress_class),
        1,
    ) == 2


def test_stall_kills_at_threshold() -> None:
    status, reason = _classify(streak=2, threshold=2)
    assert status == "no_progress"
    assert "decision progress" in reason


def test_stall_below_threshold_continues() -> None:
    status, _ = _classify(streak=1, threshold=2)
    assert status is None


def test_stall_disabled_when_threshold_zero() -> None:
    status, _ = _classify(streak=100, threshold=0)
    assert status is None


def test_max_rounds_wins_on_final_round() -> None:
    # On the very last round, the loop's own max_rounds fallthrough must win
    # the terminal label instead of a stall kill.
    status, _ = _classify(streak=50, threshold=8, round_index=500, max_rounds=500)
    assert status is None


def test_decision_timeout_ends_at_round_boundary() -> None:
    status, reason = _classify(
        streak=1,
        threshold=2,
        decision_idle_seconds=1800,
        decision_timeout_seconds=1800,
    )

    assert status == "no_progress"
    assert "1800 seconds without decision progress" in reason


def test_decision_timeout_below_limit_continues() -> None:
    status, _ = _classify(
        streak=1,
        threshold=2,
        decision_idle_seconds=1799,
        decision_timeout_seconds=1800,
    )

    assert status is None


def test_supervised_background_wait_pauses_decision_clock() -> None:
    from argus_skill.engineer.runner import _pause_decision_clock

    assert _pause_decision_clock(100.0, 1800.0) == 1900.0


@pytest.mark.parametrize(
    "research_status",
    [
        "research_incomplete",
        "paused_no_breakthrough",
        "exhausted_current_methods",
    ],
)
def test_research_pause_status_ends_current_cycle(research_status: str) -> None:
    status, reason = SupervisedEngineer._classify(
        review=_review(research_status),
        no_progress_streak=0,
        no_progress_threshold=2,
        round_index=1,
        max_rounds=500,
    )

    assert status == research_status
    assert reason == "r"
