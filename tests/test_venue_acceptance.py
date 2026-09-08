from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.core.venue_review import (
    configure_venue_revisions,
    current_venue_acceptance_issue,
    enforce_venue_acceptance,
    normalize_venue_review,
    paper_review_snapshot,
    requires_venue_review,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.reviewer._parsing import decision_from_payload, parse_decision_text
from argus_skill.skills.vertical_select import persist_vertical


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
        return RunnerResult(exit_code=0, role_decisions=[{"role": "reviewer", "payload": {
            "status": "done", "reason": "Assessed current paper.", "next_action": "",
            "venue_review": self.report,
        }}])


@pytest.mark.parametrize("report", [None, assessment(venue="NeurIPS")])
def test_missing_or_wrong_venue_response_retries_reviewer_without_engineer_work(paper, report):
    runner = _Runner(report)
    review = Reviewer(runner).evaluate(
        objective="Certify the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), vertical_state_root=str(paper)),
    )
    assert review.backend_unavailable
    assert not review.final_submission_certified
    assert "Retry the independent Reviewer" in review.next_action
    assert not (paper / "paper/REVIEW.md").exists()
    assert "currently selected venue: ICLR" in runner.prompt


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


def test_quality_revision_continues_beyond_round_limits_without_disabling_stops():
    from argus_skill.engineer.round_config import SupervisedConfig
    from argus_skill.engineer.round_settlement import RoundSettlementMixin

    config = SupervisedConfig(max_rounds=50, stall_threshold=4, soft_round_limit=12, hard_escalate_rounds=24)
    configure_venue_revisions(config)
    assert config.max_rounds == 0
    assert config.require_independent_review
    kwargs = {
        "no_progress_streak": 0, "no_progress_threshold": config.no_progress_threshold,
        "semantic_stall_streak": 100, "stall_threshold": config.stall_threshold,
        "round_index": 100, "max_rounds": config.max_rounds,
        "hard_escalate_rounds": config.hard_escalate_rounds,
        "decision_idle_seconds": 100000, "decision_timeout_seconds": config.decision_progress_timeout_seconds,
    }
    rejecting = ReviewDecision(status="continue", reason="Still below weak accept.", next_action="Run the missing matched control.")
    assert RoundSettlementMixin._classify(review=rejecting, **kwargs) == (None, "")
    stopped = ReviewDecision(status="blocked", reason="Operator stopped this work.", next_action="", operator_question="Wait for the operator.")
    assert RoundSettlementMixin._classify(review=stopped, **kwargs)[0] == "blocked"
    unavailable = ReviewDecision(status="blocked", reason="Provider unavailable.", next_action="Retry provider.", backend_unavailable=True)
    assert RoundSettlementMixin._classify(review=unavailable, **kwargs)[0] == "infra_blocked"


def test_stage_certificate_keeps_venue_rating_and_expires_on_figure_changes(paper):
    from types import SimpleNamespace

    from argus_skill.core.stage_certificate import latest_stage_review, record_stage_review

    record_stage_review(
        state_root=paper, project_root=paper, stage="review", item=SimpleNamespace(id="final-review"),
        manager_action="complete", venue_review=assessment(), venue_review_snapshot=paper_review_snapshot(paper),
    )
    assert latest_stage_review(paper, "review")["certified"] is True
    (paper / "paper/figures/method.pdf").write_bytes(b"A revised figure")
    record = latest_stage_review(paper, "review")
    assert record["certified"] is False
    assert "changed" in record["stale_reason"]
