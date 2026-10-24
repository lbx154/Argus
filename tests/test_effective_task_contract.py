from __future__ import annotations

from argus.reviewer import Reviewer


def _contract() -> str:
    from argus.roles.task_contract import EFFECTIVE_TASK_CONTRACT

    return EFFECTIVE_TASK_CONTRACT


def test_effective_task_contract_is_compact() -> None:
    contract = _contract()

    # 480 -> 600. Not this branch's growth: main's fb4ab8de added "Never stop at
    # a bare launch verdict; say what failed, why, and what should happen next
    # in plain language" (106 chars) to the constant and left the ceiling at
    # 480, so the guard has been failing on main independently of this merge.
    # The sentence earns its place — a bare verdict is the failure mode it names
    # — so the ceiling moves to match rather than the sentence coming out.
    assert len(contract) <= 600
    assert 'operator>objective>mission>preregistration' in contract
    assert "preregistration" in contract
    assert "not substitutes" in contract
    # A conflict is handed on in prose; no bare token for a parser that does
    # not exist, which then leaked verbatim into operator-visible text.
    assert "hand it to whoever can decide" in contract
    assert "`" not in contract
    for ban in ("unrelated cleanup", "never unchanged work", "Limit output", "Git retries"):
        assert ban not in contract


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
