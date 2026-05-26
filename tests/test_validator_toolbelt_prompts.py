from __future__ import annotations

from argus_skill.loop import SkillLoop


def test_engineer_prompt_includes_validator_toolbelt() -> None:
    prompt = SkillLoop._build_engineer_prompt(
        task="Update the EMNLP paper.",
        skill_text="",
        next_action=None,
        extra_guidance=None,
    )

    assert "Validator toolbelt (engineer)" in prompt
    assert "validate-full-scale-evidence --project-root ." in prompt
    assert "refresh-manifest --project-root ." in prompt
    assert "validate-full-emnlp --project-root ." in prompt
    assert "## Verification (verbatim)" in prompt
