from __future__ import annotations

from argus_skill.skills.pipeline_contracts import (
    cli_command_handlers,
)
from argus_skill.skills.pipeline_contracts import (
    main as pipeline_contracts_main,
)

# The artifact build/repair utilities are NOT quality gates; skills instruct the
# agent to run them to construct manifest/freshness/policy artifacts (and forbid
# hand-editing those JSON files), so they must stay reachable on the CLI even
# though the validate-* gates are retired.
_UTILITY_COMMANDS = {
    "refresh-manifest",
    "refresh-artifact-freshness",
    "write-validation-priority-policy",
    "repair-emnlp-contract-artifacts",
}


def test_utility_commands_are_registered() -> None:
    handlers = cli_command_handlers()
    assert _UTILITY_COMMANDS <= set(handlers)


def test_refresh_manifest_builds_artifact(tmp_path) -> None:
    # refresh-manifest must actually do work (bootstrap the manifest), not
    # silently no-op like the retired validate-* gates.
    exit_code = pipeline_contracts_main(
        ["refresh-manifest", "--project-root", str(tmp_path)]
    )
    assert exit_code in {0, 1}
    assert (tmp_path / "paper" / "ARTIFACT_MANIFEST.json").exists()


def test_validate_subcommands_remain_retired_noops(tmp_path, capsys) -> None:
    handlers = cli_command_handlers()
    for retired in (
        "validate-full-emnlp",
        "validate-manifest",
        "validate-full-scale-evidence",
        "validate-paper-contract",
    ):
        assert retired not in handlers
        exit_code = pipeline_contracts_main(
            [retired, "--project-root", str(tmp_path)]
        )
        assert exit_code == 0
    out = capsys.readouterr().out
    assert "validate-*` CLI subcommands have been retired" in out
