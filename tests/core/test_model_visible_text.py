from __future__ import annotations

from argus_skill.core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
    sanitize_model_judgment_text,
    sanitize_model_visible_text,
)
from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._parsing import parse_decision_text
from argus_skill.roles.prompts.engineer import assemble_round_prompt, build_mission_prompt
from argus_skill.roles.prompts.manager import assemble_manager_prompt, build_quick_reply_prompt
from argus_skill.roles.prompts.planner import build_bounded_dag_prompt

SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT_ID = "9f2c1b4a8e7d0000000000000000000000000000"


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


def test_hash_only_reviewer_blocker_can_only_downgrade_to_continue() -> None:
    decision = parse_decision_text(
        "STATUS=blocked\n"
        "REASON=The measured result satisfies the objective. "
        f"However, the artifact hash {SHA_A} is stale and mismatches {SHA_B}.\n"
        f"NEXT_ACTION=Refresh the stale checksum {SHA_A}.\n"
        "OPERATOR_QUESTION=none\n"
        "FORWARD_PROGRESS=true\n"
        "PLAN_SIGNAL=continue\n"
    )

    assert decision is not None
    assert decision.status == "continue"
    assert decision.final_submission_certified is False
    assert decision.next_action == ""
    assert SHA_A not in decision.reason
    assert "hash" not in decision.reason.casefold()
    assert "Policy note:" in decision.reason


def test_checksum_failure_blocker_is_not_erased_or_certified() -> None:
    reason = (
        "Two of the twelve unit tests fail because the checksum helper was never "
        "wired in."
    )
    decision = parse_decision_text(
        f"STATUS=blocked\nREASON={reason}\nNEXT_ACTION=\nOPERATOR_QUESTION=\n"
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.reason == reason
    assert decision.final_submission_certified is False


def test_material_blocker_recognizes_inability_phrasings() -> None:
    for wording in (
        "could not reproduce",
        "was not able to reproduce",
        "unable to reproduce",
    ):
        decision = parse_decision_text(
            "STATUS=blocked\n"
            f"REASON=The checksum failed, so I {wording} the reported speedup.\n"
            "NEXT_ACTION=\nOPERATOR_QUESTION=\n"
        )

        assert decision is not None
        assert decision.status == "blocked"
        assert wording in decision.reason


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


def test_operator_commit_id_survives_all_role_prompt_assembly() -> None:
    task = f"Reproduce the bug at commit {COMMIT_ID} and fix it."
    engineer = build_mission_prompt(
        task=task,
        skill_text="",
        next_action=None,
    )
    planner = build_bounded_dag_prompt(task)
    manager = assemble_manager_prompt(task)
    reviewer = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective=task,
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary=f"The current artifact is {SHA_B}",
        main_error=None,
        prior_checkpoint={},
    )

    for prompt in (engineer, planner, manager, reviewer):
        assert task in prompt
        assert COMMIT_ID in prompt
    for prompt in (manager, reviewer):
        assert MODEL_INTEGRITY_BOUNDARY.splitlines()[0] in prompt


def test_host_evidence_is_sanitized_before_prompt_assembly() -> None:
    prompt = assemble_round_prompt(
        f"Operator task keeps commit {COMMIT_ID}",
        checkpoint_block=f"Host evidence digest: {SHA_A}",
    )

    assert COMMIT_ID in prompt
    assert SHA_A not in prompt
    assert "machine-integrity-metadata omitted" in prompt


def test_manager_reply_style_matches_operator_requested_depth() -> None:
    prompt = build_quick_reply_prompt(objective="Explain the tradeoff in depth.")

    assert "match the depth, tone, and detail" in prompt
    assert "Keep it short" not in prompt


def test_reviewer_requires_causal_performance_evidence() -> None:
    prompt = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="Review the claimed throughput bottleneck.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="The end-to-end threshold was missed.",
        main_error=None,
        prior_checkpoint={},
    )

    assert "threshold miss only shows that this run missed its target" in prompt
    assert "root-cause, dominant/bottleneck-stage" in prompt
    assert "profiling, timing, or a controlled comparison" in prompt
