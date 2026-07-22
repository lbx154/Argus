"""Skill matcher prompt role separation and size budget."""
from __future__ import annotations

from argus_skill.skills.skill_prompts import Prompts

MATCHER_PROMPT_BUDGET = 15_000


def _candidate(index: int) -> dict:
    return {
        "candidate_id": f"candidate-{index}",
        "name": f"Candidate Skill {index}",
        "description": "Reusable capability description " + ("detail " * 100),
        "category": "research",
        "role": "engineer",
        "task_history": [
            "A very long historical task that helped BM25 prefiltering " * 20,
            "Another one-off historical task " * 20,
        ],
    }


def test_matcher_prompt_excludes_author_contract_and_task_history() -> None:
    prompt = Prompts.skill_match(
        "Select a reusable playbook for a bounded mathematical source audit.",
        [_candidate(index) for index in range(30)],
        requesting_role="engineer",
        primary_pool=frozenset({"engineer", "general"}),
    )

    assert "Skill Authoring Guide" not in prompt
    assert "Argus author role skill" not in prompt
    assert "past tasks:" not in prompt
    assert "A very long historical task" not in prompt


def test_matcher_prompt_has_bounded_candidate_descriptions() -> None:
    prompt = Prompts.skill_match(
        "Select a reusable playbook for a bounded mathematical source audit.",
        [_candidate(index) for index in range(30)],
        requesting_role="engineer",
        primary_pool=frozenset({"engineer", "general"}),
    )

    assert len(prompt) < MATCHER_PROMPT_BUDGET
    assert prompt.count("- ID `candidate-") == 30
    assert "Reusable capability description" in prompt
    assert "…" in prompt
