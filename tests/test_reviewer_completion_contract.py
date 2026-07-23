from __future__ import annotations

from argus_skill.core.models import ReviewDecision


def test_done_is_the_reviewers_completion_judgment() -> None:
    review = ReviewDecision(
        status="done",
        reason="The requested result is complete and supported by the inspected evidence.",
        next_action="",
    )

    assert review.final_submission_certified is True


def test_non_done_does_not_certify_completion() -> None:
    for status in ("continue", "blocked", "replan_requested"):
        review = ReviewDecision(status=status, reason="Not complete.", next_action="Act.")
        assert review.final_submission_certified is False


def test_review_event_contains_only_verdict_and_runtime_metadata() -> None:
    review = ReviewDecision(
        status="done",
        reason="Complete.",
        next_action="",
        operator_question="",
    )

    payload = review.to_event_payload()
    assert payload["status"] == "done"
    for removed in (
        "scientific_decision",
        "planner_report",
        "progress_class",
        "failure_layer",
        "failure_source",
        "control_action",
        "routing_decision",
        "checklist",
        "certification_payload",
        "checklist_feedback",
    ):
        assert removed not in payload
