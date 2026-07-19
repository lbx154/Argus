from __future__ import annotations

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import SupervisedEngineer
from argus_skill.reviewer._parsing import parse_decision_text
from argus_skill.reviewer.failure_taxonomy import resolve_failure_layer


def test_legacy_causes_resolve_to_typed_layers() -> None:
    assert resolve_failure_layer(failure_layer="", failure_cause="environmental") == "platform"
    assert resolve_failure_layer(failure_layer="", failure_cause="execution_mistake") == "orchestration"
    assert resolve_failure_layer(failure_layer="", failure_cause="method_failure") == "scientific"


def test_parser_preserves_explicit_failure_layer() -> None:
    decision = parse_decision_text(
        '{"status":"continue","reason":"scorer broke","next_action":"repair scorer",'
        '"failure_cause":"execution_mistake","failure_layer":"evaluator",'
        '"progress_class":"setup_only","control":null,"verification_summary":"",'
        '"review_source":"reviewer","achievement":null,"scope":"",'
        '"checklist":[],"research_result":null,"planner_report":{},'
        '"checkpoint":{},"skill_ops":[],"wiki_ops":[],"checklist_feedback":{},'
        '"step_back":null,"operator_question":"","round_summary_markdown":"# Review",'
        '"completion_summary_markdown":""}'
    )
    assert decision is not None
    assert decision.failure_layer == "evaluator"


def test_platform_block_replans_instead_of_killing_idea() -> None:
    review = ReviewDecision(
        status="blocked",
        reason="the exact interpreter cannot import the runner",
        next_action="repair the project platform",
        failure_cause="environmental",
        failure_layer="platform",
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
    assert "scientific state is unchanged" in reason


def test_scientific_block_remains_a_scientific_decision() -> None:
    review = ReviewDecision(
        status="blocked",
        reason="valid experiment falsified the mechanism",
        next_action="synthesize the negative result",
        failure_cause="method_failure",
        failure_layer="scientific",
    )
    status, _reason = SupervisedEngineer._classify(
        review=review,
        no_progress_streak=0,
        no_progress_threshold=2,
        semantic_stall_streak=0,
        stall_threshold=4,
        round_index=1,
        max_rounds=10,
    )
    assert status == "blocked"
