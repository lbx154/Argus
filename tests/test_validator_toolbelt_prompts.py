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
    # Core gates still advertised after toolbelt slim-down:
    assert "validate-pipeline --project-root ." in prompt
    assert "validate-grounding --project-root ." in prompt
    assert "validate-full-scale-evidence --project-root ." in prompt
    assert "validate-paper-contract --project-root ." in prompt
    assert "validate-paper-format --project-root ." in prompt
    assert "validate-submission --project-root ." in prompt
    assert "validate-full-emnlp --project-root ." in prompt
    # Retired bureaucratic / mutating helpers MUST NOT be advertised:
    for retired in (
        "refresh-manifest",
        "write-validation-priority-policy",
        "refresh-artifact-freshness",
        "repair-emnlp-contract-artifacts",
        "validate-image2-figures",
        "validate-layout-review",
        "validate-academic-language-review",
        "validate-paper-infrastructure-review",
    ):
        assert f"{retired} --project-root ." not in prompt, (
            f"engineer prompt still advertises retired tool {retired!r}"
        )
    assert "## Verification (verbatim)" in prompt
    assert "Long-horizon paper execution contract" in prompt
    assert "AGENTS.md" in prompt
    assert "7.5-8 main-content" in prompt


def test_engineer_prompt_omits_paper_contract_for_generic_task() -> None:
    prompt = SkillLoop._build_engineer_prompt(
        task="Fix the CLI argument parser.",
        skill_text="",
        next_action=None,
        extra_guidance=None,
    )

    assert "Long-horizon paper execution contract" not in prompt
