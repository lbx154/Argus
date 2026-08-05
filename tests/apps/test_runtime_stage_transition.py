from __future__ import annotations

from argus_skill.apps._runtime_helpers import _should_run_stage_transition


def test_non_stage_closing_planner_node_cannot_move_pipeline_stage() -> None:
    assert not _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        require_independent_review=True,
        review_source="reviewer",
        preplanned=True,
        stage_closing=False,
    )


def test_stage_closing_planner_node_reaches_manager_stage_writer() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        require_independent_review=True,
        review_source="reviewer",
        preplanned=True,
        stage_closing=True,
    )


def test_direct_reviewed_work_preserves_legacy_stage_transition() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        review_source="reviewer",
        preplanned=False,
        stage_closing=False,
    )


def test_final_submission_is_stage_eligible_without_bounded_flag() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="final_submission",
        review_source="reviewer",
        preplanned=True,
        stage_closing=False,
    )
