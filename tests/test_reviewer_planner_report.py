from __future__ import annotations

import pytest

from argus.core.models import LoopOutcome, RoundRecord
from argus.reviewer._parsing import decision_from_payload, parse_decision_text


def test_named_reviewer_verdict_preserves_planner_report() -> None:
    decision = parse_decision_text(
        "STATUS=done\n"
        "REASON=The bounded implementation is correct but does not close the target gap.\n"
        "NEXT_ACTION=Replace the low-impact direction.\n"
        "OPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=false\n"
        "PLAN_SIGNAL=reconsider\n"
        "PLAN_CHALLENGE=The skip-zero candidate is no longer required.\n"
        "PLAN_ALTERNATIVE=Use the no-gap validator.\n"
        "AUTHORITY_IMPACT=technical\n"
    )

    assert decision is not None
    assert decision.status == "done"
    assert decision.planner_report == {
        "forward_progress": False,
        "plan_signal": "reconsider",
        "challenge": "The skip-zero candidate is no longer required.",
        "alternative": "Use the no-gap validator.",
        "authority_impact": "technical",
    }
    event = decision.to_event_payload()
    assert event["plan_challenge"] == "The skip-zero candidate is no longer required."
    assert event["plan_alternative"] == "Use the no-gap validator."
    assert "planner_report" not in event


def test_legacy_json_reviewer_verdict_preserves_planner_report() -> None:
    decision = parse_decision_text(
        '{"status":"continue","reason":"more work","next_action":"pivot",'
        '"planner_report":{"forward_progress":true,"plan_signal":"continue"}}'
    )

    assert decision is not None
    assert decision.planner_report["forward_progress"] is True


def _mixed_payload(nested):
    return {
        "status": "done",
        "reason": "The bounded result is correct; the next phase needs a new comparison.",
        "next_action": "",
        "planner_report": nested,
        "forward_progress": True,
        "plan_signal": "reconsider",
        "plan_challenge": "The current plan cannot answer the original question.",
        "plan_alternative": "Run the matched causal comparison.",
        "authority_impact": "technical",
    }


@pytest.mark.parametrize("nested", [None, {}, {"forward_progress": False}])
def test_missing_nested_fields_preserve_the_flat_plan_challenge(nested) -> None:
    decision = decision_from_payload(_mixed_payload(nested))

    assert decision is not None
    assert decision.planner_report == {
        "forward_progress": False if nested else True,
        "plan_signal": "reconsider",
        "challenge": "The current plan cannot answer the original question.",
        "alternative": "Run the matched causal comparison.",
        "authority_impact": "technical",
    }
    assert decision.to_event_payload()["plan_challenge"] == (
        "The current plan cannot answer the original question."
    )


@pytest.mark.parametrize("empty", [None, ""])
@pytest.mark.parametrize("use_aliases", [False, True])
def test_explicit_nested_empty_values_do_not_restore_flat_advice(empty, use_aliases) -> None:
    nested = {
        "forward_progress": False,
        "plan_signal": None,
        "plan_challenge" if use_aliases else "challenge": empty,
        "plan_alternative" if use_aliases else "alternative": empty,
        "authority_impact": empty,
    }
    decision = decision_from_payload(_mixed_payload(nested))

    assert decision is not None
    assert decision.planner_report == {"forward_progress": False}


def test_nested_none_progress_does_not_restore_flat_true() -> None:
    decision = decision_from_payload(_mixed_payload({"forward_progress": None}))
    assert decision is not None
    assert "forward_progress" not in decision.planner_report
    assert decision.planner_report["plan_signal"] == "reconsider"


def test_loop_outcome_exposes_final_reviewer_planner_report() -> None:
    decision = parse_decision_text(
        "STATUS=done\nREASON=complete\nNEXT_ACTION=\nOPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=false\nPLAN_SIGNAL=reconsider\n"
    )
    assert decision is not None
    outcome = LoopOutcome(
        status="done",
        rounds=[
            RoundRecord(
                round_index=1,
                engineer_message="done",
                engineer_exit_code=0,
                review=decision,
            )
        ],
        final_message="done",
        reason="complete",
        workdir="/tmp",
    )

    assert outcome.final_planner_report == {
        "forward_progress": False,
        "plan_signal": "reconsider",
    }
