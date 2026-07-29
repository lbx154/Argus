from __future__ import annotations

from argus_skill.core.models import LoopOutcome, RoundRecord
from argus_skill.reviewer._parsing import parse_decision_text


def test_named_reviewer_verdict_preserves_planner_report() -> None:
    decision = parse_decision_text(
        "STATUS=done\n"
        "REASON=The bounded implementation is correct but does not close the target gap.\n"
        "NEXT_ACTION=Replace the low-impact direction.\n"
        "OPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=false\n"
        "PLAN_SIGNAL=reconsider\n"
    )

    assert decision is not None
    assert decision.status == "done"
    assert decision.planner_report == {
        "forward_progress": False,
        "plan_signal": "reconsider",
    }
    assert "planner_report" not in decision.to_event_payload()


def test_legacy_json_reviewer_verdict_preserves_planner_report() -> None:
    decision = parse_decision_text(
        '{"status":"continue","reason":"more work","next_action":"pivot",'
        '"planner_report":{"forward_progress":true,"plan_signal":"continue"}}'
    )

    assert decision is not None
    assert decision.planner_report["forward_progress"] is True


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
        skill_used=None,
        skill_distilled=False,
        final_message="done",
        reason="complete",
        workdir="/tmp",
    )

    assert outcome.final_planner_report == {
        "forward_progress": False,
        "plan_signal": "reconsider",
    }
