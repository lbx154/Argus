from __future__ import annotations

import json

from argus_skill.skills.pipeline_contracts import cli_command_handlers, main as pipeline_contracts_main
from argus_skill.tools.validator_toolbelt import (
    format_validator_toolbelt_for_role,
    all_validator_tools,
    main,
    validator_tools_for_role,
)


def test_validator_toolbelt_is_empty() -> None:
    """The validator toolbelt is intentionally retired.

    The agent surface is now stage-checklist driven; the empty tuple is
    the canonical signal that nothing is to be advertised. Any caller
    iterating ``all_validator_tools()`` must cleanly get nothing.
    """

    assert all_validator_tools() == ()
    assert validator_tools_for_role("engineer", include_mutating=True) == ()
    assert validator_tools_for_role("reviewer") == ()
    assert validator_tools_for_role("critic") == ()
    assert validator_tools_for_role("planner") == ()


def test_format_validator_toolbelt_returns_empty_string() -> None:
    """When no tools are advertised the toolbelt formatter must collapse
    to an empty string so the calling prompt does not get a confusing
    headline with no content underneath it.
    """

    for role in ("engineer", "reviewer", "critic", "planner"):
        assert format_validator_toolbelt_for_role(role) == ""


def test_cli_lists_role_tools_as_text(capsys) -> None:
    """The CLI helper still runs, but with no tools advertised the body
    is empty — agents that ask for the list see nothing instead of a
    stale catalog.
    """

    assert main(["list", "--role", "planner", "--stage", "experiments"]) == 0
    out = capsys.readouterr().out
    # No tool ids should appear; the headline / hint block is acceptable
    # but no historical validate-* identifier should leak.
    for retired in (
        "validate-pipeline",
        "validate-grounding",
        "validate-full-scale-evidence",
        "validate-paper-contract",
        "validate-paper-format",
        "validate-submission",
        "validate-full-emnlp",
        "validate-academic-language-review",
        "validate-layout-review",
        "refresh-manifest",
    ):
        assert retired not in out, f"retired tool {retired!r} leaked into CLI output"


def test_cli_lists_role_tools_as_json(capsys) -> None:
    """JSON form of the listing must be an empty array now."""

    assert main(["list", "--role", "reviewer", "--stage", "review", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


def test_all_validator_toolbelt_commands_are_registered_in_pipeline_contracts_cli() -> None:
    registered = set(cli_command_handlers())
    toolbelt_ids = {tool.id for tool in all_validator_tools()}

    assert toolbelt_ids <= registered


def test_pipeline_contracts_cli_accepts_every_validator_toolbelt_command(tmp_path) -> None:
    for tool in all_validator_tools():
        exit_code = pipeline_contracts_main([tool.id, "--project-root", str(tmp_path)])
        assert exit_code in {0, 1}
