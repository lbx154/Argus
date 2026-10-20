"""A host intervention sits beside a Reviewer verdict and keeps its judgments.

``manager_attention`` and ``learning`` describe the verdict the Reviewer wrote.
The host no longer rewrites that verdict: an intervention records what the
host did beside it, so the Reviewer's routing judgments still apply. Only a
review the host must retry, because its venue assessment is missing, drops
them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.models import REVIEW_ROUTING_JUDGMENTS, ReviewDecision
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.venue_review import (
    ACCEPTANCE_BAR_POLICY,
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
        planner_report={**JUDGED, **report},
    )


def _assert_kept(review: ReviewDecision) -> None:
    assert {key: review.planner_report[key] for key in JUDGED} == JUDGED
    assert review.to_event_payload()["manager_attention"] == "not_needed"


def test_the_operator_question_policy_keeps_the_verdict_and_its_judgments():
    from argus.engineer.round_config import QUESTION_POLICY, SupervisedConfig
    from argus.engineer.round_settlement import _apply_operator_question_policy
    from argus.engineer.round_state import RoundLoopState

    review = _review("blocked")
    review.operator_question = "Which dataset may we use?"
    config = SupervisedConfig(operator_questions_allowed=False)

    recorded = _apply_operator_question_policy(review, supervised_config=config, state=RoundLoopState())

    assert recorded.status == "blocked"
    assert recorded.operator_question == "Which dataset may we use?"
    assert recorded.intervention(QUESTION_POLICY)
    _assert_kept(recorded)


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


def test_a_final_review_replan_stays_the_reviewers(paper):
    review = _review("replan_requested", plan_signal="reconsider", challenge="Wrong baseline.")

    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)

    assert review.status == "replan_requested"
    assert review.planner_report["challenge"] == "Wrong baseline."
    assert not review.interventions
    _assert_kept(review)


def test_a_venue_done_below_the_bar_stays_done_beside_an_intervention(paper):
    review = _review("done")
    review.venue_review = {
        "venue": "ICLR 2027", "recommendation": "weak_reject", "acceptance_clear": False,
        "rationale": "Missing a matched baseline.", "blocking_issues": ["Missing baseline."],
    }

    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)

    assert review.status == "done"
    assert review.reason == "The table is correct."
    assert not review.final_submission_certified
    assert review.intervention(ACCEPTANCE_BAR_POLICY)["action"] == "kept_open"
    _assert_kept(review)


def test_a_missing_venue_assessment_drops_the_judgments(paper):
    review = _review("done")
    review.venue_review = {"venue": "Other venue", "recommendation": "accept"}

    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)

    assert review.status == "blocked"
    assert not set(REVIEW_ROUTING_JUDGMENTS) & set(review.planner_report)
    assert "manager_attention" not in review.to_event_payload()
