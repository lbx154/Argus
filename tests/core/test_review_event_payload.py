from __future__ import annotations

from argus_skill.core.models import ReviewDecision


def test_review_event_carries_minimal_verdict() -> None:
    payload = ReviewDecision(
        status="continue",
        reason="More evidence is needed.",
        next_action="Run the decisive experiment.",
        operator_question="",
    ).to_event_payload()

    assert payload["status"] == "continue"
    assert payload["reason"] == "More evidence is needed."
    assert payload["next_action"] == "Run the decisive experiment."
    assert payload["operator_question"] == ""


def test_review_event_omits_removed_control_fields() -> None:
    payload = ReviewDecision(
        status="done",
        reason="Complete.",
        next_action="",
    ).to_event_payload()

    for removed in (
        "scientific_decision",
        "planner_report",
        "progress_class",
        "failure_source",
        "failure_layer",
        "control_action",
        "checklist",
        "certification_payload",
        "checklist_feedback",
        "achievement",
    ):
        assert removed not in payload


def test_event_callsite_extras_remain_supported() -> None:
    payload = ReviewDecision(
        status="done",
        reason="Complete.",
        next_action="",
    ).to_event_payload(round_index=2, text="review complete")

    assert payload["round_index"] == 2
    assert payload["text"] == "review complete"
