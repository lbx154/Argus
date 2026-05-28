from __future__ import annotations

import json

from argus_skill.skills.pipeline_contracts import cli_command_handlers, main as pipeline_contracts_main
from argus_skill.tools.validator_toolbelt import (
    FINAL_EMNLP_COMMAND,
    format_validator_toolbelt_for_role,
    get_validator_tool,
    all_validator_tools,
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


def test_role_filter_separates_engineer_and_reviewer_tools() -> None:
    """The slimmed toolbelt no longer exposes mutating repair tools or the
    LLM-review chrome at all (those validators were too noisy a feedback
    loop for the agent). Surface the core gates the engineer/reviewer
    actually need, and assert the historical mutating tools are gone for
    *every* role so nothing slips back in by mistake.
    """

    engineer_ids = {tool.id for tool in validator_tools_for_role("engineer", include_mutating=True)}
    reviewer_ids = {tool.id for tool in validator_tools_for_role("reviewer")}

    # Mutating repair tools are intentionally dropped from the toolbelt.
    for retired in (
        "refresh-manifest",
        "write-validation-priority-policy",
        "refresh-artifact-freshness",
        "repair-emnlp-contract-artifacts",
    ):
        assert retired not in engineer_ids, f"{retired} should be off the toolbelt"
        assert retired not in reviewer_ids, f"{retired} should be off the toolbelt"

    # LLM-review chrome is intentionally dropped from the toolbelt too.
    for retired in (
        "validate-academic-language-review",
        "validate-layout-review",
        "validate-paper-infrastructure-review",
        "validate-image2-figures",
    ):
        assert retired not in engineer_ids, f"{retired} should be off the toolbelt"
        assert retired not in reviewer_ids, f"{retired} should be off the toolbelt"

    # Core gates must still be available to both roles.
    for required in (
        "validate-pipeline",
        "validate-grounding",
        "validate-full-scale-evidence",
        "validate-paper-contract",
        "validate-paper-format",
        "validate-submission",
        "validate-full-emnlp",
    ):
        assert required in engineer_ids
        assert required in reviewer_ids


def test_toolbelt_prompt_distinguishes_narrow_feedback_from_final_gate() -> None:
    text = format_validator_toolbelt_for_role("reviewer")

    assert "Validator toolbelt (reviewer)" in text
    assert "Run the narrowest validator" in text
    assert "not substitutes for final readiness" in text
    # The reviewer surface still advertises the core paper-contract gate
    # and the final EMNLP gate, even though the LLM-review chrome is gone.
    assert "validate-paper-contract --project-root ." in text
    assert "validate-full-emnlp --project-root ." in text


def test_cli_lists_role_tools_as_text(capsys) -> None:
    assert main(["list", "--role", "planner", "--stage", "experiments"]) == 0
    out = capsys.readouterr().out

    assert "Validator toolbelt (planner)" in out
    assert "validate-full-scale-evidence --project-root ." in out
    assert "validate-grounding" not in out


def test_cli_lists_role_tools_as_json(capsys) -> None:
    """The slim ``review`` phase no longer exposes the three LLM-review
    validators on the toolbelt. Listing the reviewer/review role-phase
    pair should therefore return an empty set — agents that want those
    deeper review reports can still call the python entrypoints
    directly, but they will not be advertised in the prompt.
    """

    assert main(["list", "--role", "reviewer", "--stage", "review", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in payload}

    assert ids == set()
    assert all("command" in item for item in payload)


def test_all_validator_toolbelt_commands_are_registered_in_pipeline_contracts_cli() -> None:
    registered = set(cli_command_handlers())
    toolbelt_ids = {tool.id for tool in all_validator_tools()}

    assert toolbelt_ids <= registered


def test_pipeline_contracts_cli_accepts_every_validator_toolbelt_command(tmp_path) -> None:
    for tool in all_validator_tools():
        exit_code = pipeline_contracts_main([tool.id, "--project-root", str(tmp_path)])
        assert exit_code in {0, 1}
