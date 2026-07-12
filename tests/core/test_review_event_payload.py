"""Regression test for ReviewDecision.to_event_payload.

Pre-fix bug: runner.py + mission/engine.py both built the
``round.review.completed`` event dict by hand, copying only 6 of the
11 reviewer JSON schema fields. ``checklist`` (per-item structured
eval), ``planner_report`` (planner-facing structured briefing),
``scope``, ``checkpoint``, and ``verification_summary``
were silently dropped. Postmortem of "why did the reviewer let an
underbaked draft pass?" was impossible from events.jsonl alone.

This test pins the new contract: the helper forwards every field the
schema requires, plus any caller-supplied extras (round_max, session_id,
text, review_skipped) without dropping the structured payload.
"""
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
        scope="final_submission",
        checklist=[
            {"item": "BibTeX has 10+ verified entries", "satisfied": False, "evidence": "0 entries"},
            {"item": "RESEARCH_BRIEF.md exists", "satisfied": True, "evidence": "exists, 4.2 KB"},
        ],
        planner_report={
            "forward_progress": True,
            "headline": "Literature scaffold landed; BibTeX still empty.",
            "blocker": "refs.bib has 0 verified @ entries.",
            "recommended_next": "Run /paper-write Step 4 with DBLP_BIBTEX=true.",
            "evidence_files": [{"path": "paper/refs.bib", "why": "missing entries"}],
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
    )


def test_to_event_payload_forwards_every_structured_field() -> None:
    review = _full_review()
    payload = review.to_event_payload(round_index=2, round_max=5,
                                      text="review: continue")

    # Type + the structured fields runner.py forwards.
    assert payload["type"] == "round.review.completed"
    assert payload["status"] == "continue"
    assert "confidence" not in payload
    assert payload["round_summary_markdown"].startswith("- evidence:")
    assert payload["completion_summary_markdown"] == "Mission not complete."
    assert payload["failure_cause"] == "skill_gap"

    # Previously dropped fields — these are the regression guard.
    assert payload["verification_summary"].startswith("`jq")
    assert payload["scope"] == "final_submission"
    assert isinstance(payload["checklist"], list) and len(payload["checklist"]) == 2
    assert payload["checklist"][0]["item"].startswith("BibTeX")
    assert payload["planner_report"]["headline"].startswith("Literature scaffold")
    assert payload["planner_report"]["evidence_files"][0]["path"] == "paper/refs.bib"
    assert payload["checkpoint"]["goal"].startswith("EMNLP")

    # Token bookkeeping preserved.
    assert payload["input_tokens"] == 12345
    assert payload["cached_input_tokens"] == 2000
    assert payload["output_tokens"] == 789
    assert payload["reasoning_output_tokens"] == 456
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
    assert payload["checkpoint"] == {}
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
        "scope", "checkpoint", "verification_summary",
    ):
        assert k in payload
