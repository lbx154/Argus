"""Engineer fatal-error classification: a dead Codex/Copilot subprocess is a
transient backend failure, so the engineer retries in a fresh session instead
of burning a full reviewer round on a no-output turn.

Regression for the gpt-5.5-on-fnyweg live run where the engineer's Codex
process intermittently exited 2 ("Process exited with code 2 before turn
completion.") and, because that string was not in the backend-failure pattern
list, every crash wasted a whole reviewer round (mission stalled in research).
"""
from argus_skill.engineer.runner import fatal_error_looks_like_backend_failure as _is_bf


def test_codex_subprocess_death_is_backend_failure() -> None:
    # The exact fatal_error agent_cli_runner emits on a nonzero Codex exit.
    assert _is_bf("Process exited with code 2 before turn completion.")
    assert _is_bf("Process exited with code 1 before turn completion.")
    # Copilot CLI variant.
    assert _is_bf("Copilot CLI exited with code 1.")
    # Sanity: the pre-existing transport patterns still match.
    assert _is_bf("Too Many Requests 429")
    assert _is_bf("gateway timeout")


def test_backend_failure_does_not_misclassify() -> None:
    # Recoverable reconnect notices must NOT become a backend-failure state
    # (the CLI keeps recovering), even though they mention a disconnect.
    assert not _is_bf("Reconnecting... 1/100 (stream disconnected before completion)")
    # Normal model prose / check output must never trip this.
    assert not _is_bf("research artifacts are still missing")
    # Intentional daemon shutdown is its own category, not a backend failure.
    assert not _is_bf("External interrupt: daemon stop requested")
    assert not _is_bf(None)
    assert not _is_bf("")
