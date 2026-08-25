from __future__ import annotations

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.reviewer._parsing import parse_decision_text


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


def test_prose_only_trailing_footer_falls_back_to_named_verdict() -> None:
    review = parse_decision_text(
        "STATUS=done\n"
        "REASON=The requested outcome is complete.\n"
        "Final decision:\n"
        "Approved."
    )

    assert review is not None
    assert review.status == "done"


def test_operator_options_are_scoped_to_the_final_footer() -> None:
    review = parse_decision_text(
        "A quoted draft contained:\n"
        "OPERATOR_OPTIONS=a::Stop::This is not the verdict\n"
        "Decision:\n"
        "STATUS=done\n"
        "REASON=The requested outcome is complete.\n"
        "NEXT_ACTION=\n"
    )

    assert review is not None
    assert review.operator_options == []


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
        "checklist",
        "certification_payload",
        "checklist_feedback",
    ):
        assert removed not in payload


class _ReviewerRunner:
    backend = "pi"
    tool_activity_observation_supported = True

    def __init__(self, *, tool_activity: bool) -> None:
        self.tool_activity = tool_activity

    def run_exec(self, **_kwargs) -> RunnerResult:
        return RunnerResult(
            exit_code=0,
            role_decisions=[{
                "role": "reviewer",
                "payload": {
                    "status": "done",
                    "reason": "The requested outcome is complete.",
                    "next_action": "",
                    "forward_progress": True,
                    "plan_signal": "continue",
                },
            }],
            tool_activity_observed=self.tool_activity,
        )


class _EmptyReviewerRunner(_ReviewerRunner):
    def run_exec(self, **_kwargs) -> RunnerResult:
        return RunnerResult(exit_code=0, agent_messages=[])


def _evaluate(runner: _ReviewerRunner, tmp_path) -> ReviewDecision:
    return Reviewer(runner).evaluate(
        objective="Verify the completed task.",
        round_index=1,
        session_id=None,
        main_summary="The implementation is complete.",
        main_error=None,
        config=ReviewerConfig(model="m", working_dir=str(tmp_path)),
    )


def test_done_is_not_rewritten_into_continue_for_tool_ceremony(tmp_path) -> None:
    decision = _evaluate(_ReviewerRunner(tool_activity=False), tmp_path)

    assert decision.status == "done"


def test_empty_reviewer_output_does_not_create_engineer_work(tmp_path) -> None:
    decision = _evaluate(_EmptyReviewerRunner(tool_activity=False), tmp_path)

    assert decision.status == "blocked"
    assert decision.backend_unavailable is True
    assert "says nothing about the Engineer" in decision.reason
    assert "Retry Reviewer" in decision.next_action


def test_done_remains_reviewer_judgment_after_real_tool_activity(tmp_path) -> None:
    decision = _evaluate(_ReviewerRunner(tool_activity=True), tmp_path)

    assert decision.status == "done"


def test_backend_without_reliable_tool_telemetry_is_not_trapped(tmp_path) -> None:
    runner = _ReviewerRunner(tool_activity=False)
    runner.tool_activity_observation_supported = False

    assert _evaluate(runner, tmp_path).status == "done"
