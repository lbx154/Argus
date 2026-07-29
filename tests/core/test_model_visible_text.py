from __future__ import annotations

from argus_skill.core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
    sanitize_model_judgment_text,
    sanitize_model_visible_text,
)
from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._parsing import parse_decision_text
from argus_skill.roles.prompts.engineer import build_mission_prompt
from argus_skill.roles.prompts.manager import assemble_manager_prompt
from argus_skill.roles.prompts.planner import build_bounded_dag_prompt

SHA_A = "a" * 64
SHA_B = "b" * 64


def test_model_visible_text_redacts_opaque_identifiers() -> None:
    text = sanitize_model_visible_text(
        f"submission_sha256={SHA_A}; bare={SHA_B}; commit=506bef34bc4c"
    )

    assert SHA_A not in text
    assert SHA_B not in text
    assert "506bef34bc4c" not in text
    assert "machine-integrity-metadata omitted" in text


def test_model_judgment_drops_hash_comparison_but_keeps_semantics() -> None:
    text = sanitize_model_judgment_text(
        "The measured result satisfies the objective. "
        f"However, the manifest hash {SHA_A} is stale and mismatches {SHA_B}."
    )

    assert text == "The measured result satisfies the objective."


def test_hash_only_reviewer_blocker_cannot_keep_round_open() -> None:
    decision = parse_decision_text(
        "STATUS=continue\n"
        "REASON=The measured result satisfies the objective. "
        f"However, the artifact hash {SHA_A} is stale and mismatches {SHA_B}.\n"
        f"NEXT_ACTION=Refresh the stale checksum {SHA_A}.\n"
        "OPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=true\n"
        "PLAN_SIGNAL=continue\n"
    )

    assert decision is not None
    assert decision.status == "done"
    assert decision.next_action == ""
    assert SHA_A not in decision.reason
    assert "hash" not in decision.reason.casefold()


def test_substantive_blocker_survives_hash_clause_removal() -> None:
    decision = parse_decision_text(
        "STATUS=continue\n"
        "REASON=The required test still fails. "
        f"The artifact hash {SHA_A} also mismatches {SHA_B}.\n"
        "NEXT_ACTION=Fix the failing test. Refresh the stale digest.\n"
        "OPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=false\n"
        "PLAN_SIGNAL=continue\n"
    )

    assert decision is not None
    assert decision.status == "continue"
    assert decision.reason == "The required test still fails."
    assert decision.next_action == "Fix the failing test."


def test_all_role_prompt_boundaries_hide_dynamic_hash_values() -> None:
    engineer = build_mission_prompt(
        task=f"Repair artifact {SHA_A}",
        skill_text="",
        next_action=None,
    )
    planner = build_bounded_dag_prompt(f"Repair artifact {SHA_A}")
    manager = assemble_manager_prompt(f"Judge artifact {SHA_A}")
    reviewer = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective=f"Judge artifact {SHA_A}",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary=f"The current artifact is {SHA_B}",
        main_error=None,
        prior_checkpoint={},
    )

    assert SHA_A not in engineer
    assert SHA_A not in planner
    for prompt in (manager, reviewer):
        assert MODEL_INTEGRITY_BOUNDARY.splitlines()[0] in prompt
        assert SHA_A not in prompt
