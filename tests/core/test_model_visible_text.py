from __future__ import annotations

from argus.core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
)
from argus.reviewer import Reviewer
from argus.roles.prompts.engineer import build_mission_prompt
from argus.roles.prompts.manager import assemble_manager_prompt

SHA_B = "b" * 64
COMMIT_ID = "9f2c1b4a8e7d0000000000000000000000000000"


def test_operator_commit_id_survives_all_role_prompt_assembly() -> None:
    task = f"Reproduce the bug at commit {COMMIT_ID} and fix it."
    engineer = build_mission_prompt(
        task=task,
        skill_text="",
        next_action=None,
    )
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

    for prompt in (engineer, manager, reviewer):
        assert task in prompt
        assert COMMIT_ID in prompt
    for prompt in (manager, reviewer):
        assert MODEL_INTEGRITY_BOUNDARY.splitlines()[0] in prompt


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

    assert 'a missed threshold describes only that run' in prompt
    assert 'Root-cause, dominant-stage, bottleneck, or replacement claims' in prompt
    assert "profiling, timing, or a controlled comparison" in prompt
