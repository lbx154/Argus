from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import SupervisedEngineer
from argus_skill.life.context_packet import record_reviewed_handoff
from argus_skill.reviewer import RESEARCH_SCHEMA_PATH, SCHEMA_PATH, Reviewer
from argus_skill.reviewer._parsing import parse_decision_text


def routing_payload(decision: str = "return_to_planner") -> dict[str, object]:
    return {
        "status": "continue",
        "reason": "The implementation produced a new decision frontier.",
        "next_action": "Preserve the reviewed evidence.",
        "routing_decision": decision,
        "routing_reason": "L4 must choose between two evidence-backed methods.",
        "routing_handoff": "The feasibility probe is complete and sealed.",
    }


def test_active_reviewer_schemas_require_routing_shadow_fields() -> None:
    for schema_path in (SCHEMA_PATH, RESEARCH_SCHEMA_PATH):
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        properties = schema["properties"]

        assert properties["routing_decision"]["enum"] == [
            "",
            "keep_mission",
            "return_to_planner",
        ]
        assert {
            "routing_decision",
            "routing_reason",
            "routing_handoff",
        }.issubset(schema["required"])


def test_routing_fields_append_without_shifting_legacy_positions() -> None:
    assert [field.name for field in fields(ReviewDecision)][-3:] == [
        "routing_decision",
        "routing_reason",
        "routing_handoff",
    ]


def test_routing_shadow_parses_and_persists_in_review_event() -> None:
    decision = parse_decision_text(json.dumps(routing_payload()))

    assert decision is not None
    assert decision.routing_decision == "return_to_planner"
    assert decision.routing_reason == (
        "L4 must choose between two evidence-backed methods."
    )
    assert decision.routing_handoff == (
        "The feasibility probe is complete and sealed."
    )
    event = decision.to_event_payload(round_index=2)
    assert event["routing_decision"] == "return_to_planner"
    assert event["routing_reason"] == decision.routing_reason
    assert event["routing_handoff"] == decision.routing_handoff


def test_terminal_review_clears_even_nonempty_routing_shadow() -> None:
    payload = routing_payload("return_to_planner")
    payload.update({
        "status": "done",
        "next_action": "",
    })

    decision = parse_decision_text(json.dumps(payload))

    assert decision is not None
    assert decision.status == "done"
    assert decision.routing_decision == ""
    assert decision.routing_reason == ""
    assert decision.routing_handoff == ""


def test_routing_shadow_never_changes_round_classification() -> None:
    review = ReviewDecision(
        status="continue",
        reason="One engineer-fixable gap remains.",
        next_action="Repair the gap.",
        routing_decision="return_to_planner",
        routing_reason="A Planner decision would be useful.",
        routing_handoff="The current evidence is sealed.",
        planner_report={
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [],
        },
    )

    status, reason = SupervisedEngineer._classify(
        review=review,
        no_progress_streak=0,
        no_progress_threshold=2,
        round_index=1,
        max_rounds=3,
    )

    assert status is None
    assert reason == ""


def test_routing_shadow_is_event_only_not_context_packet(tmp_path: Path) -> None:
    mission_path = tmp_path / "handoffs" / "mission-1" / "mission.json"
    mission_path.parent.mkdir(parents=True)
    mission_path.write_text("{}\n", encoding="utf-8")
    review = ReviewDecision(
        status="continue",
        reason="A normal repair remains.",
        next_action="Repair it.",
        routing_decision="return_to_planner",
        routing_reason="L4 judgment may be useful.",
        routing_handoff="The feasibility result is sealed.",
    )

    path = record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=review,
        checkpoint_path=None,
    )

    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "routing_decision" not in payload["review"]
    assert "routing_reason" not in payload["review"]
    assert "routing_handoff" not in payload["review"]


def test_reviewer_prompt_defines_shadow_without_token_objective() -> None:
    prompt = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="continue the current proof mission",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="A feasibility probe completed.",
        main_error=None,
        prior_checkpoint={},
    )

    assert "`routing_decision` is shadow-only" in prompt
    assert "`keep_mission`" in prompt
    assert "`return_to_planner`" in prompt
    assert "most informative when both stay `continue`" in prompt
    assert "leave them empty otherwise" in prompt
    assert "Never decide from token, cost, or round-count thresholds." in prompt
