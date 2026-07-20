from __future__ import annotations

from argus_skill.core.claim_synthesis import build_claim_synthesis
from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import SupervisedEngineer
from argus_skill.life.supervisor._planner_rendering import PlannerRenderingMixin


def _result(result_class: str) -> dict:
    return {
        "result_class": result_class,
        "correctness_status": "verified",
        "novelty_status": "unverified",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
        "evidence": ["experiments/run/scores.json"],
        "limitations": ["one model family"],
    }


def test_verified_negative_does_not_auto_route_to_write_up() -> None:
    packet = build_claim_synthesis(
        research_result=_result("structured_failure_report"),
        planner_report={"headline": "Reasoning hurts on the measured regime."},
    )
    assert packet is not None
    assert packet["route"] == "supported_negative"
    assert packet["action"] == "diagnose_or_pivot"
    assert packet["advance_to_analysis_or_report"] is False


def test_verified_boundary_needs_explicit_publication_judgment() -> None:
    packet = build_claim_synthesis(
        research_result=_result("partial_result"),
        planner_report={"headline": "The mechanism helps only on long contexts."},
        scientific_decision="go",
    )
    assert packet is not None
    assert packet["route"] == "supported_boundary"
    assert packet["action"] == "develop_publication_thesis"
    assert packet["advance_to_analysis_or_report"] is True


def test_scientific_negative_replans_to_diagnosis_not_drafting() -> None:
    review = ReviewDecision(
        status="blocked",
        reason="the registered positive effect was not observed",
        next_action="write the measured negative result",
        failure_cause="method_failure",
        failure_layer="scientific",
        research_result=_result("structured_failure_report"),
        planner_report={"headline": "No improvement under the registered protocol."},
    )
    status, reason = SupervisedEngineer._classify(
        review=review,
        no_progress_streak=0,
        no_progress_threshold=2,
        semantic_stall_streak=0,
        stall_threshold=4,
        round_index=1,
        max_rounds=10,
    )
    assert status == "replan_requested"
    assert "diagnose_or_pivot" in reason
    assert "do not auto-draft" in reason


def test_planner_rendering_surfaces_claim_and_evidence() -> None:
    rendered = PlannerRenderingMixin._render_claim_synthesis({
        "route": "supported_negative",
        "action": "diagnose_or_pivot",
        "headline": "A valid null result changes the paper claim.",
        "evidence": ["experiments/run/scores.json"],
        "advance_to_analysis_or_report": False,
    })
    assert "advance_to_analysis_or_report=false" in rendered
    assert "scores.json" in rendered
