from __future__ import annotations

import json

from argus_skill.tools.validator_toolbelt import (
    FINAL_EMNLP_COMMAND,
    format_validator_toolbelt_for_role,
    get_validator_tool,
    main,
    validator_tools_for_role,
)


def test_validator_tool_commands_use_pipeline_contracts_surface() -> None:
    tool = get_validator_tool("validate-full-scale-evidence")

    assert tool.command(".") == (
        "python -m argus_skill.skills.pipeline_contracts "
        "validate-full-scale-evidence --project-root ."
    )
    assert FINAL_EMNLP_COMMAND == (
        "python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root ."
    )


def test_role_filter_keeps_mutating_tools_engineer_only() -> None:
    engineer_ids = {tool.id for tool in validator_tools_for_role("engineer", include_mutating=True)}
    reviewer_ids = {tool.id for tool in validator_tools_for_role("reviewer")}

    assert "refresh-manifest" in engineer_ids
    assert "write-validation-priority-policy" in engineer_ids
    assert "refresh-artifact-freshness" in engineer_ids
    assert "refresh-manifest" not in reviewer_ids
    assert "write-validation-priority-policy" not in reviewer_ids
    assert "refresh-artifact-freshness" not in reviewer_ids
    assert "validate-academic-language-review" in reviewer_ids
    assert "validate-full-emnlp" in reviewer_ids


def test_toolbelt_prompt_distinguishes_narrow_feedback_from_final_gate() -> None:
    text = format_validator_toolbelt_for_role("reviewer")

    assert "Validator toolbelt (reviewer)" in text
    assert "Run the narrowest validator" in text
    assert "not substitutes for final readiness" in text
    assert "validate-academic-language-review --project-root ." in text
    assert "validate-full-emnlp --project-root ." in text


def test_cli_lists_role_tools_as_text(capsys) -> None:
    assert main(["list", "--role", "planner", "--stage", "experiments"]) == 0
    out = capsys.readouterr().out

    assert "Validator toolbelt (planner)" in out
    assert "validate-full-scale-evidence --project-root ." in out
    assert "validate-grounding" not in out


def test_cli_lists_role_tools_as_json(capsys) -> None:
    assert main(["list", "--role", "reviewer", "--stage", "review", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in payload}

    assert ids == {"validate-layout-review", "validate-academic-language-review"}
    assert all("command" in item for item in payload)
