"""Review events persist control once and omit duplicate prose projections."""
from __future__ import annotations

from argus_skill.core.models import ReviewDecision


def _full_review() -> ReviewDecision:
    return ReviewDecision(
        status="continue",
        reason="Acceptance check failed on BibTeX entries.",
        next_action="Add real BibTeX from Semantic Scholar before rerunning.",
        round_summary_markdown="- evidence: jq OK\n- blocker: BibTeX missing\n",
        completion_summary_markdown="Mission not complete.",
        failure_cause="skill_gap",
        verification_summary="`jq . research/LITERATURE_GROUNDING.json` returned OK.",
        control_action="wait_for_subagent",
        control_task_id="train-1",
        scope="final_submission",
        checklist=[
            {"item": "BibTeX has 10+ verified entries", "satisfied": False, "evidence": "0 entries"},
            {"item": "RESEARCH_BRIEF.md exists", "satisfied": True, "evidence": "exists, 4.2 KB"},
        ],
        planner_report={
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [{"path": "paper/refs.bib", "why": "missing entries"}],
            "headline": "legacy duplicate must be filtered",
        },
        checkpoint={
            "goal": "EMNLP short paper, train-free",
            "done": ["scaffold", "literature_grounding"],
            "next_step": "fetch verified BibTeX",
        },
        input_tokens=12345,
        cached_input_tokens=2000,
        output_tokens=789,
        reasoning_output_tokens=456,
        prompt_block_stats={
            "static_total": {
                "chars": 4000,
                "bytes": 4200,
                "estimated_tokens": 1050,
            }
        },
    )


def test_to_event_payload_forwards_every_structured_field() -> None:
    review = _full_review()
    payload = review.to_event_payload(round_index=2, round_max=5,
                                      text="review: continue")

    # Type + the structured fields runner.py forwards.
    assert payload["type"] == "round.review.completed"
    assert payload["status"] == "continue"
    assert "confidence" not in payload
    assert payload["failure_cause"] == "skill_gap"

    assert payload["control_action"] == "wait_for_subagent"
    assert payload["control_task_id"] == "train-1"
    assert payload["scope"] == "final_submission"
    assert isinstance(payload["checklist"], list) and len(payload["checklist"]) == 2
    assert payload["checklist"][0]["item"].startswith("BibTeX")
    assert payload["planner_report"]["plan_signal"] == "continue"
    assert payload["planner_report"]["evidence_files"][0]["path"] == "paper/refs.bib"
    assert "headline" not in payload["planner_report"]
    for duplicate in (
        "round_summary_markdown",
        "completion_summary_markdown",
        "verification_summary",
        "checkpoint",
        "step_back",
    ):
        assert duplicate not in payload

    # Token bookkeeping preserved.
    assert payload["input_tokens"] == 12345
    assert payload["cached_input_tokens"] == 2000
    assert payload["output_tokens"] == 789
    assert payload["reasoning_output_tokens"] == 456
    assert payload["prompt_block_stats"]["static_total"]["estimated_tokens"] == 1050
    assert payload["usage_scope"] == "delta"

    # Caller-supplied extras passed through.
    assert payload["round_index"] == 2
    assert payload["round_max"] == 5
    assert payload["text"].startswith("review: continue")


def test_to_event_payload_forwards_operator_question() -> None:
    review = ReviewDecision(
        status="blocked", reason="r", next_action="n",
        operator_question="刷哪两道题？",
    )
    assert review.to_event_payload()["operator_question"] == "刷哪两道题？"


def test_to_event_payload_forwards_reviewer_achievement() -> None:
    review = ReviewDecision(
        status="done",
        reason="official benchmark verified",
        next_action="",
        achievement={
            "title": "Kernel gain certified",
            "goal": "Optimize the kernel",
            "metric_id": "sol-percent",
            "summary": "Official scorer improved.",
            "evidence": ["experiments/result.json"],
        },
    )

    assert review.to_event_payload()["achievement"]["metric_id"] == "sol-percent"


def test_to_event_payload_handles_empty_synthesized_verdict() -> None:
    """The daemon-stop / backend-failure synthesized verdicts have
    empty structured fields and zero tokens. Helper must not crash and
    must emit consistent shape so consumers can rely on keys existing."""
    review = ReviewDecision(
        status="error",
        reason="daemon stop", next_action="",
    )
    payload = review.to_event_payload(
        round_index=1, round_max=3, text="review: skipped",
        review_skipped=True,
    )
    assert payload["checklist"] == []
    assert payload["planner_report"] == {}
    assert payload["achievement"] is None
    assert payload["input_tokens"] == 0
    assert payload["reasoning_output_tokens"] == 0
    assert payload["review_skipped"] is True


def test_to_event_payload_extras_can_override_helpers_but_not_lose_data() -> None:
    """Extras dict is merged last — verify caller can attach
    session_id / round_max without dropping any reviewer field."""
    review = _full_review()
    payload = review.to_event_payload(
        round_index=2, session_id="mission-abc",
    )
    assert payload["session_id"] == "mission-abc"
    assert payload["round_index"] == 2
    # All reviewer fields still there.
    for k in (
        "status", "checklist", "planner_report",
        "scope",
        "control_action", "control_task_id",
    ):
        assert k in payload
