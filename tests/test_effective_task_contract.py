from __future__ import annotations

from argus_skill.loop import SkillLoop
from argus_skill.reviewer import Reviewer


def _contract() -> str:
    from argus_skill.core.task_contract import EFFECTIVE_TASK_CONTRACT

    return EFFECTIVE_TASK_CONTRACT


def test_effective_task_contract_is_compact() -> None:
    contract = _contract()

    assert len(contract) <= 480
    assert "Current operator" in contract
    assert "preregistration" in contract
    assert "ambiguous_objective" in contract


def test_engineer_prompt_includes_shared_effective_task_contract() -> None:
    prompt = SkillLoop._build_engineer_prompt(
        task="Run the authorized source search.",
        skill_text="",
        next_action=None,
        original_request="Find one real verified instance.",
    )

    assert _contract() in prompt
    assert "non-negotiable north star" not in prompt
    assert "under the Effective task contract" in prompt


def test_reviewer_prompt_includes_shared_effective_task_contract() -> None:
    reviewer = Reviewer(runner=None, skill_store=None)

    prompt = reviewer._build_prompt(
        objective="Run the authorized source search.",
        original_objective="Find one real verified instance.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="Search executed and evidence saved.",
        main_error=None,
        prior_checkpoint={},
    )

    assert _contract() in prompt
    assert "Original operator request (immutable anchor)" not in prompt
