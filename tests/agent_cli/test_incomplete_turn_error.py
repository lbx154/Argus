from argus_skill.agent_cli.agent_cli_runner import _incomplete_turn_error


def test_incomplete_turn_prefers_explicit_cli_error() -> None:
    assert _incomplete_turn_error([
        "warning: loading configuration",
        'Error: Model "gpt5.6" from --model flag is not available.',
    ]) == 'Error: Model "gpt5.6" from --model flag is not available.'


def test_incomplete_turn_without_stderr_is_still_a_failure() -> None:
    assert "without completing" in _incomplete_turn_error([])
