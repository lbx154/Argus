"""Host rewrites of a Reviewer verdict drop the Reviewer's routing judgments.

``manager_attention`` and ``learning`` describe the verdict the Reviewer wrote.
When the host changes that verdict's status or strips its signals, they no
longer apply, so they are read as absent: the Manager looks, reflection runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.models import REVIEW_ROUTING_JUDGMENTS, ReviewDecision
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.venue_review import (
    _keep_final_repairs_in_place,
    enforce_venue_acceptance,
    paper_review_snapshot,
)
from argus.skills.vertical_select import persist_vertical

JUDGED = {
    "manager_attention": "not_needed", "manager_attention_reason": "Clear repair.",
    "learning": "nothing_new", "learning_reason": "Routine.",
}


def _review(status: str, **report) -> ReviewDecision:
    return ReviewDecision(
        status=status, reason="The table is correct.", next_action="",
        planner_report={"forward_progress": True, **JUDGED, **report},
    )


def _assert_dropped(review: ReviewDecision) -> None:
    assert not set(REVIEW_ROUTING_JUDGMENTS) & set(review.planner_report)
    assert "manager_attention" not in review.to_event_payload()


def test_the_operator_question_policy_drops_the_judgments():
    from argus.engineer.round_config import SupervisedConfig
    from argus.engineer.round_settlement import _enforce_operator_question_policy
    from argus.engineer.round_state import RoundLoopState

    review = _review("blocked")
    review.operator_question = "Which dataset may we use?"
    config = SupervisedConfig(operator_questions_allowed=False)

    rewritten = _enforce_operator_question_policy(review, supervised_config=config, state=RoundLoopState())

    assert rewritten.status == "continue"
    _assert_dropped(rewritten)


def test_a_declared_background_run_override_drops_the_judgments():
    from argus.engineer.runner import hold_review_for_pending_background_run

    rewritten = hold_review_for_pending_background_run(_review("done"))

    assert rewritten.status == "continue"
    _assert_dropped(rewritten)


def test_a_final_review_replan_kept_in_place_drops_the_judgments():
    review = _review("replan_requested", plan_signal="reconsider", challenge="Wrong baseline.")

    _keep_final_repairs_in_place(review)

    assert review.status == "continue"
    _assert_dropped(review)


def test_a_final_review_signal_strip_drops_the_judgments():
    review = _review("continue", plan_signal="reconsider", challenge="Wrong baseline.")

    _keep_final_repairs_in_place(review)

    _assert_dropped(review)


def test_the_reviewer_s_own_continue_keeps_its_judgments():
    review = _review("continue")

    _keep_final_repairs_in_place(review)

    assert review.planner_report["manager_attention"] == "not_needed"


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    persist_vertical(tmp_path, "research", target_venue="ICLR")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)
    (tmp_path / "paper/figures").mkdir(parents=True)
    (tmp_path / "paper/main.tex").write_text("Current manuscript")
    (tmp_path / "paper/main.pdf").write_bytes(b"Current rendered manuscript")
    return tmp_path


def test_a_venue_done_downgraded_to_continue_drops_the_judgments(paper):
    review = _review("done")
    review.venue_review = {
        "venue": "ICLR 2027", "recommendation": "weak_reject", "acceptance_clear": False,
        "rationale": "Missing a matched baseline.", "blocking_issues": ["Missing baseline."],
    }

    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)

    assert review.status == "continue"
    _assert_dropped(review)


def test_a_missing_venue_assessment_drops_the_judgments(paper):
    review = _review("done")
    review.venue_review = {"venue": "Other venue", "recommendation": "accept"}

    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)

    assert review.status == "blocked"
    _assert_dropped(review)
