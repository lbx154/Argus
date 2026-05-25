from __future__ import annotations

from argus_skill.scientist.prompts import Prompts


def test_engineer_prompt_includes_argus_role_skill() -> None:
    prompt = Prompts.execute("fix the parser", in_git_repo=True)

    assert "Argus engineer role skill" in prompt
    assert "Argus Engineer Role" in prompt
    assert "execution arm" in prompt
    assert "concrete verification" in prompt


def test_scientist_prompts_include_argus_role_skill() -> None:
    match_prompt = Prompts.skill_match("fix flaky tests", [])
    distill_prompt = Prompts.distill("fix flaky tests")

    for prompt in (match_prompt, distill_prompt):
        assert "Argus scientist role skill" in prompt
        assert "Argus Scientist Role" in prompt
        assert "skill-memory researcher" in prompt
        assert "Match skills conservatively" in prompt
        assert "gpt-5.4-mini" in prompt
        assert "relatively small engineer model" in prompt


def test_scientist_distillation_prompt_targets_small_engineer_model() -> None:
    prompt = Prompts.distill("add a CSV importer")

    assert "`gpt-5.4-mini`, a relatively small engineer model" in prompt
    assert "explicit enough for that smaller model to execute without guessing" in prompt
    assert "validation commands" in prompt
