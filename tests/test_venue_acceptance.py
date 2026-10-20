from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.models import ReviewDecision, RunnerResult
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.role_tool_bridge import bridge_request
from argus.core.venue_review import (
    configure_venue_revisions,
    current_venue_acceptance_issue,
    enforce_venue_acceptance,
    normalize_venue_review,
    paper_review_snapshot,
    requires_venue_review,
    selected_acceptance_minimum,
    venue_review_issue,
)
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer._parsing import decision_from_payload, parse_decision_text
from argus.skills.vertical_select import persist_vertical


def assessment(recommendation="weak_accept", *, clear=True, issues=None, venue="ICLR 2027"):
    return {
        "venue": venue, "recommendation": recommendation,
        "acceptance_clear": clear,
        "rationale": "The matched controls establish a useful new systems boundary; the stated scope is supported.",
        "blocking_issues": [] if issues is None else issues,
    }


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    persist_vertical(tmp_path, "research", target_venue="ICLR")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    state["current_verdict"] = "in_progress"
    write_pipeline_state(tmp_path, state)
    (tmp_path / "paper/figures").mkdir(parents=True)
    (tmp_path / "paper/main.tex").write_text("Current manuscript")
    (tmp_path / "paper/main.pdf").write_bytes(b"Current rendered manuscript")
    (tmp_path / "paper/figures/method.pdf").write_bytes(b"Current figure")
    return tmp_path


@pytest.mark.parametrize("rating", ["strong_reject", "reject", "weak_reject", "borderline"])
def test_rejection_cannot_pass_even_with_done(paper, rating):
    review = ReviewDecision(status="done", reason="The local edit is fixed.", next_action="", venue_review=assessment(rating))
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.status == "continue"
    assert not review.final_submission_certified
    assert not review.backend_unavailable
    assert review.venue_review["recommendation"] == rating
    assert review.reason == "The local edit is fixed."
    assert review.next_action


@pytest.mark.parametrize("rating", ["weak_accept", "accept", "strong_accept", "best_paper"])
def test_explicit_clear_acceptance_is_bound_to_current_paper(paper, rating):
    review = ReviewDecision(status="done", reason="Acceptance is supported.", next_action="", venue_review=assessment(rating))
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.final_submission_certified
    assert current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper) == ""
    payload = review.to_event_payload()
    assert payload["venue_review"]["recommendation"] == rating
    assert payload["venue_review_snapshot"] == paper_review_snapshot(paper)


@pytest.mark.parametrize("rating", ["weak_accept", "accept", "strong_accept", "best_paper"])
def test_strong_accept_goal_uses_the_real_rating_without_upgrading_it(paper, rating):
    state = read_pipeline_state(paper)
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(paper, state)
    reason = "The current result is sound; a held-out mechanism test can establish its wider value."
    review = ReviewDecision(status="done", reason=reason, next_action="", venue_review=assessment(rating))

    enforce_venue_acceptance(
        review, venue="ICLR", minimum=selected_acceptance_minimum(paper),
        before=paper_review_snapshot(paper), artifact_root=paper,
    )

    sufficient = rating in {"strong_accept", "best_paper"}
    assert review.final_submission_certified is sufficient
    assert review.status == ("done" if sufficient else "continue")
    assert review.venue_review["recommendation"] == rating
    assert review.reason == reason
    assert not review.backend_unavailable
    assert bool(current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)) is not sufficient
    if not sufficient:
        assert "actual strong accept quality" in review.next_action
    assert read_pipeline_state(paper)["current_stage"] == "review"


def test_raising_the_operator_bar_invalidates_a_previous_weak_accept(paper):
    review = ReviewDecision(status="done", reason="Current paper accepted.", next_action="", venue_review=assessment())
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper) == ""

    state = read_pipeline_state(paper)
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(paper, state)

    assert "strong_accept or better" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)
    assert review.venue_review["recommendation"] == "weak_accept"


def test_invalid_explicit_acceptance_minimum_cannot_silently_lower_the_bar(paper):
    state = read_pipeline_state(paper)
    state["venue_acceptance_minimum"] = "typo"
    write_pipeline_state(paper, state)
    assert "invalid operator" in venue_review_issue(
        assessment("best_paper"), venue="ICLR", minimum=selected_acceptance_minimum(paper),
    )


@pytest.mark.parametrize("report", [assessment(clear=False), assessment(issues=["Missing strongest matched baseline."])])
def test_uncertain_acceptance_or_unresolved_reject_issue_requires_revision(paper, report):
    review = ReviewDecision(status="done", reason="Mostly ready.", next_action="", venue_review=report)
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.status == "continue"
    assert not review.final_submission_certified


@pytest.mark.parametrize("changed", ["main.tex", "main.pdf", "figures/method.pdf"])
def test_source_pdf_or_figure_changes_invalidate_acceptance(paper, changed):
    review = ReviewDecision(status="done", reason="Accepted.", next_action="", venue_review=assessment())
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    (paper / "paper" / changed).write_bytes(b"New unreviewed bytes")
    assert "changed" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)


def test_manuscript_change_during_review_cannot_be_certified(paper):
    before = paper_review_snapshot(paper)
    (paper / "paper/main.pdf").write_bytes(b"Concurrent new PDF")
    review = ReviewDecision(status="done", reason="Accepted old PDF.", next_action="", venue_review=assessment())
    enforce_venue_acceptance(review, venue="ICLR", before=before, artifact_root=paper)
    assert review.status == "continue"
    assert not review.final_submission_certified


def test_final_scientific_repair_preserves_experiment_plan_without_replanning(paper):
    review = ReviewDecision(
        status="replan_requested", reason="The baseline processes fewer positions.",
        next_action="Measure latency and accepted tokens under the same speculative budget.",
        venue_review=assessment("weak_reject", clear=False),
        planner_report={
            "plan_signal": "reconsider", "challenge": "Unequal useful work invalidates the speed comparison.",
            "alternative": "Hold accepted token yield fixed, run matched-budget controls, and report throughput.",
            "forward_progress": True, "authority_impact": "technical",
        },
    )
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.status == "continue"
    assert "Hold accepted token yield fixed" in review.next_action
    assert "Measure latency and accepted tokens" in review.next_action
    assert "current final Review" in review.next_action
    assert review.planner_report == {"forward_progress": True}
    assert read_pipeline_state(paper)["current_stage"] == "review"
    assert not review.final_submission_certified


def test_named_and_structured_reviews_preserve_rating_but_not_model_claimed_bindings():
    report = {**assessment(), "sha256": "model-invented"}
    footer = "STATUS=done\nREASON=Reviewed paper.\nNEXT_ACTION=\nVENUE_REVIEW=" + json.dumps(report, indent=2)
    named = parse_decision_text(footer)
    structured = decision_from_payload({"status": "done", "reason": "Reviewed paper.", "next_action": "", "venue_review": report, "venue_review_passed": True})
    assert named is not None and structured is not None
    assert named.venue_review == structured.venue_review == assessment()
    assert not structured.venue_review_passed
    assert structured.venue_review_snapshot is None
    assert normalize_venue_review({**report, "recommendation": "borderline_accept"}) is None


class _Runner:
    backend = "pi"

    def __init__(self, report):
        self.report = report
        self.prompt = ""

    def run_exec(self, **kwargs):
        self.prompt = kwargs["prompt"]
        payload = {"review": "Assessed current paper."}
        if self.report:
            payload["recommendation"] = self.report["recommendation"]
        bridge_request("ARGUS_PLUGIN_REVIEW", "approve_review", payload, env=kwargs["options"].extension_env)
        return RunnerResult(exit_code=0)


@pytest.mark.parametrize("report", [None, assessment(venue="NeurIPS")])
def test_missing_or_wrong_venue_response_retries_reviewer_without_engineer_work(paper, report):
    review = ReviewDecision(status="done", reason="Assessed current paper.", next_action="", venue_review=report)
    enforce_venue_acceptance(
        review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper,
    )
    assert review.backend_unavailable
    assert not review.final_submission_certified
    assert "Retry the independent Reviewer" in review.next_action
    assert not (paper / "paper/REVIEW.md").exists()


def test_integrated_reviewer_persists_the_actual_venue_recommendation(paper):
    review = Reviewer(_Runner(assessment())).evaluate(
        objective="Certify the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), vertical_state_root=str(paper)),
    )
    assert review.final_submission_certified
    assert "Recommendation: weak_accept" in (paper / "paper/REVIEW.md").read_text()
    assert current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper) == ""


def test_reviewer_uses_the_selected_venue_from_a_separate_state_directory(paper):
    state_root = paper / "session-state"
    persist_vertical(state_root, "research", target_venue="ICLR")
    state = read_pipeline_state(state_root)
    state["current_stage"] = "review"
    write_pipeline_state(state_root, state)
    review = Reviewer(_Runner(assessment())).evaluate(
        objective="Certify the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), artifact_root=str(paper), vertical_state_root=str(state_root)),
    )
    assert review.final_submission_certified
    assert current_venue_acceptance_issue(review, state_root=state_root, artifact_root=paper) == ""


def test_formal_reviewer_gets_the_operator_bar_from_control_state(paper):
    state_root = paper / "session-state"
    persist_vertical(state_root, "research", target_venue="ICLR")
    state = read_pipeline_state(state_root)
    state.update(current_stage="review", venue_acceptance_minimum="strong_accept")
    write_pipeline_state(state_root, state)
    runner = _Runner(assessment())
    config = ReviewerConfig(
        active_vertical="research", working_dir=str(paper),
        artifact_root=str(paper), vertical_state_root=str(state_root),
    )
    review = Reviewer(runner).evaluate(
        objective="Improve this paper to strong acceptance", round_index=1,
        session_id=None, main_summary="Stronger evidence added.", main_error=None,
        scope="final_submission", config=config,
    )
    assert "completion bar is strong accept" in runner.prompt
    assert "recommendation: clearly weak_accept or better" not in runner.prompt
    assert review.status == "continue"
    assert not review.final_submission_certified
    assert "Recommendation: weak_accept" in (paper / "paper/REVIEW.md").read_text()
    prior_fingerprint = review.static_fingerprint

    state["venue_acceptance_minimum"] = "weak_accept"
    write_pipeline_state(state_root, state)
    review = Reviewer(runner).evaluate(
        objective="Judge the current paper", round_index=2, session_id=None,
        main_summary="No paper changes", main_error=None, scope="final_submission",
        config=config, resume_thread_id="old-session", prior_static_fingerprint=prior_fingerprint,
    )
    assert review.final_submission_certified
    assert review.static_fingerprint != prior_fingerprint
    assert not review.session_resumed


def test_unclassified_nonpaper_review_does_not_invent_a_venue_requirement(tmp_path):
    review = Reviewer(_Runner(None)).evaluate(
        objective="Certify the implementation", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", working_dir=str(tmp_path)),
    )
    assert review.status == "done"
    assert not review.venue_review_required


def test_gate_applies_to_final_papers_only():
    assert requires_venue_review(vertical="research", stage="review")
    assert requires_venue_review(vertical="research", stage="paper", scope="final_submission")
    assert not requires_venue_review(vertical="research", stage="paper", scope="bounded")
    assert not requires_venue_review(vertical="research", stage="review", operation="cold_read")
    assert not requires_venue_review(vertical="software", stage="review", scope="final_submission")


def test_quality_revision_runs_to_a_round_budget_without_disabling_stops():
    from argus.core.venue_review import VENUE_STALL_ROUNDS
    from argus.engineer.round_config import SupervisedConfig
    from argus.engineer.round_settlement import RoundSettlementMixin

    config = SupervisedConfig(max_rounds=50, stall_threshold=4, soft_round_limit=12, hard_escalate_rounds=24)
    configure_venue_revisions(config)
    assert config.max_rounds == 6  # the venue-review budget, smaller than the mission cap
    assert config.provider_turn_cap_streak_limit == 0
    assert config.require_independent_review
    # Operator decision, 2026-10-19: fruitless rounds stop after five and ask.
    assert config.stall_threshold == VENUE_STALL_ROUNDS and config.stall_asks_operator
    kwargs = {
        "no_progress_streak": 0, "no_progress_threshold": config.no_progress_threshold,
        "semantic_stall_streak": VENUE_STALL_ROUNDS - 1, "stall_threshold": config.stall_threshold,
        "round_index": config.max_rounds - 1, "max_rounds": config.max_rounds,
        "hard_escalate_rounds": config.hard_escalate_rounds,
        "decision_idle_seconds": 100000, "decision_timeout_seconds": config.decision_progress_timeout_seconds,
    }
    rejecting = ReviewDecision(status="continue", reason="Still below weak accept.", next_action="Run the missing matched control.")
    assert RoundSettlementMixin._classify(review=rejecting, **kwargs) == (None, "")
    stalled = dict(kwargs, semantic_stall_streak=VENUE_STALL_ROUNDS)
    assert RoundSettlementMixin._classify(review=rejecting, **stalled)[0] == "no_progress"
    stopped = ReviewDecision(status="blocked", reason="Operator stopped this work.", next_action="", operator_question="Wait for the operator.")
    assert RoundSettlementMixin._classify(review=stopped, **kwargs)[0] == "blocked"
    unavailable = ReviewDecision(status="blocked", reason="Provider unavailable.", next_action="Retry provider.", backend_unavailable=True)
    assert RoundSettlementMixin._classify(review=unavailable, **kwargs)[0] == "infra_blocked"


def test_stage_certificate_keeps_venue_rating_and_expires_on_figure_changes(paper):
    from types import SimpleNamespace

    from argus.core.stage_certificate import latest_stage_review, record_stage_review

    record_stage_review(
        state_root=paper, project_root=paper, stage="review", item=SimpleNamespace(id="final-review"),
        manager_action="complete", venue_review=assessment(), venue_review_snapshot=paper_review_snapshot(paper),
    )
    assert latest_stage_review(paper, "review")["certified"] is True
    (paper / "paper/figures/method.pdf").write_bytes(b"A revised figure")
    record = latest_stage_review(paper, "review")
    assert record["certified"] is False
    assert "changed" in record["stale_reason"]


@pytest.mark.parametrize("recommendation", ["weak_accept", "weak_reject"])
def test_reviewer_verdict_counts_when_no_venue_was_selected(paper, recommendation):
    from types import SimpleNamespace

    from argus.core.stage_certificate import latest_stage_review, record_stage_review

    state_root = paper
    state = read_pipeline_state(state_root)
    state.pop("target_venue", None)
    state.pop("venue", None)
    write_pipeline_state(state_root, state)
    runner = _Runner(assessment(recommendation))
    review = Reviewer(runner).evaluate(
        objective="Judge the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), artifact_root=str(paper), vertical_state_root=str(state_root)),
    )
    assert "No venue has been selected for this paper" in runner.prompt
    assert "(not selected)" not in runner.prompt
    # A verdict against the stated standard is a verdict: never a Reviewer backend failure.
    assert not review.backend_unavailable
    assert "omitted a valid assessment" not in (review.reason or "")
    # No venue name is invented for the record.
    assert review.venue_review["venue"] == ""
    assert review.venue_review["recommendation"] == recommendation
    report = (paper / "paper/REVIEW.md").read_text()
    assert f"Recommendation: {recommendation}" in report
    accepted = recommendation == "weak_accept"
    assert review.final_submission_certified is accepted
    # The finalizers apply the same check: an accepting verdict lets the project finish.
    issue = current_venue_acceptance_issue(review, state_root=state_root, artifact_root=paper)
    assert (issue == "") is accepted
    record_stage_review(
        state_root=state_root, project_root=paper, stage="review", item=SimpleNamespace(id="final-review"),
        manager_action="complete", venue_review=review.venue_review,
        venue_review_snapshot=review.venue_review_snapshot,
    )
    assert latest_stage_review(state_root, "review")["certified"] is accepted
    if not accepted:
        assert "selected venue" not in issue
        assert "selected venue" not in review.next_action


class _PaperEngineer:
    def __init__(self, paper: Path, *, revise: bool) -> None:
        self.paper, self.revise, self.calls = paper, revise, 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        self.calls += 1
        if self.revise:
            with (self.paper / "paper/main.tex").open("a", encoding="utf-8") as handle:
                handle.write(f"\n% revision {self.calls}\n")
        return RunnerResult(exit_code=0, agent_messages=["Revised the paper; ready for review."])


class _BelowBarReviewer:
    """A paid venue verdict below the bar, bound to the paper it read."""

    def __init__(self, paper: Path) -> None:
        self.paper, self.calls = paper, 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        return ReviewDecision(
            status="continue", reason="Still below weak accept.", next_action="Add the matched control.",
            venue_review_required=True, venue_review_snapshot=paper_review_snapshot(self.paper),
        )


def _final_review(paper: Path, *, revise: bool):
    from typing import Any, cast

    from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer

    engineer, reviewer = _PaperEngineer(paper, revise=revise), _BelowBarReviewer(paper)
    supervised = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    supervised.engineer_runner, supervised.engineer_config = engineer, EngineerConfig(model="stub")
    supervised.reviewer, supervised.reviewer_config = reviewer, ReviewerConfig(model="stub")
    config = SupervisedConfig(checkpoint_path=paper / "CHECKPOINT.md")
    configure_venue_revisions(config)
    status, _rounds, _final, reason, _thread = supervised.run(
        objective="Bring the paper to the venue bar in final Review",
        engineer_prompt_builder=lambda next_action, include_static=True: "Revise. " + (next_action or ""),
        supervised_config=config, workdir=paper,
    )
    return status, reason, engineer.calls, reviewer.calls


def test_an_unchanged_paper_is_not_reviewed_again(paper):
    status, reason, engineer_calls, reviewer_calls = _final_review(paper, revise=False)
    assert (engineer_calls, reviewer_calls) == (2, 1)
    assert status == "replan_requested"
    assert "unchanged since the last venue review" in reason


def test_a_final_review_stops_at_its_budget_and_hands_the_paper_to_the_manager(paper, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_VENUE_REVIEW_MAX_ROUNDS", "3")
    status, reason, engineer_calls, reviewer_calls = _final_review(paper, revise=True)
    assert (engineer_calls, reviewer_calls) == (3, 3)
    assert status == "blocked"
    assert "the Manager decides" in reason
