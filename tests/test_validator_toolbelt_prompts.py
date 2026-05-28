from __future__ import annotations

from argus_skill.loop import SkillLoop


def test_engineer_prompt_includes_stage_checklist() -> None:
    """The engineer prompt is now stage-checklist driven; no `validate-*`
    CLI surface is advertised. The reviewer rules against the checklist
    items directly.
    """

    prompt = SkillLoop._build_engineer_prompt(
        task="Update the EMNLP paper.",
        skill_text="",
        next_action=None,
        extra_guidance=None,
    )

    # New surface: a per-stage checklist with framing for the engineer.
    assert "## Stage checklist" in prompt
    assert "L2 reviewer will tick these items" in prompt

    # Old surface must be GONE — no toolbelt headline and no leaked
    # validate-*/refresh-*/write-* command identifiers from the
    # historical advertised set.
    assert "Validator toolbelt" not in prompt
    for retired in (
        "validate-pipeline",
        "validate-grounding",
        "validate-full-scale-evidence",
        "validate-paper-contract",
        "validate-paper-format",
        "validate-submission",
        "validate-full-emnlp",
        "validate-image2-figures",
        "validate-layout-review",
        "validate-academic-language-review",
        "validate-paper-infrastructure-review",
        "refresh-manifest",
        "write-validation-priority-policy",
        "refresh-artifact-freshness",
        "repair-emnlp-contract-artifacts",
    ):
        assert f"{retired} --project-root ." not in prompt, (
            f"engineer prompt still advertises retired CLI command {retired!r}"
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
