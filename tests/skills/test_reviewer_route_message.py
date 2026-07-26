"""The operator-facing message when the model-backed prose review can't run.

`generate_academic_language_review` hard-blocks when it cannot reach a text
reviewer, and the underlying `_require_route` guidance is "configure api_key,
base_url, and model". On a copilot- or claude-backed reviewer that advice is
impossible to act on: those backends authenticate through their own CLI and
expose no OpenAI-style HTTP route, so the operator is told to set something
that does not exist for them.

These tests pin the fix and — more importantly — its two limits: the note must
not appear when a route IS configured (a real HTTP failure would be
mislabelled), and it must never turn a blocking gate into a passing one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.verticals.research.academic_language_review import (
    describe_reviewer_route_unavailable,
)

_BOOM = RuntimeError("text reviewer route unavailable")


def _env(tmp_path: Path, *, backend: str, route: dict | None = None) -> dict[str, str]:
    """A real vault file plus the env that points at it — no mocks."""
    vault = tmp_path / "model_api.json"
    vault.write_text(
        json.dumps({"routes": {"reviewer": route}} if route else {"routes": {}}),
        encoding="utf-8",
    )
    return {
        "ARGUS_SKILL_CAPABILITY_VAULT": str(vault),
        "ARGUS_SKILL_REVIEWER_BACKEND": backend,
    }


_USABLE_ROUTE = {
    "api_key": "sk-test-not-a-real-key",
    "base_url": "https://example.invalid/v1",
    "model": "gpt-test",
}


@pytest.mark.parametrize("backend", ["copilot", "claude", "COPILOT", "Claude"])
def test_agent_cli_backend_without_a_route_gets_actionable_guidance(
    tmp_path: Path,
    backend: str,
) -> None:
    message = describe_reviewer_route_unavailable(_BOOM, _env(tmp_path, backend=backend))

    assert str(_BOOM) in message
    assert "capability-vault" in message
    assert backend.lower() in message
    # The old advice is the thing that could not be acted on; it must not be
    # the operator's only instruction.
    assert "cannot be set" in message


def test_configured_route_is_not_mislabelled_as_a_backend_problem(
    tmp_path: Path,
) -> None:
    # A genuine HTTP failure on a route the operator DID configure must not be
    # explained away as "your backend has no HTTP route".
    env = _env(tmp_path, backend="copilot", route=_USABLE_ROUTE)

    assert describe_reviewer_route_unavailable(_BOOM, env) == str(_BOOM)


@pytest.mark.parametrize("backend", ["codex", "opencode", "memory"])
def test_http_capable_backends_keep_the_original_message(
    tmp_path: Path,
    backend: str,
) -> None:
    # codex and friends CAN take api_key/base_url/model, so the original
    # guidance is correct for them and must be left alone.
    env = _env(tmp_path, backend=backend)

    assert describe_reviewer_route_unavailable(_BOOM, env) == str(_BOOM)


def test_secrets_in_the_underlying_error_are_redacted(tmp_path: Path) -> None:
    leaked = RuntimeError("401 from https://api.example.invalid key=sk-live-abcd1234efgh5678")
    message = describe_reviewer_route_unavailable(
        leaked,
        _env(tmp_path, backend="copilot"),
    )

    assert "sk-live-abcd1234efgh5678" not in message


def test_never_masks_the_underlying_error_when_lookup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The message helper runs on a path that is already failing; it must not
    # add a second failure of its own.
    import argus_skill.tools.capability_vault as vault_module

    def _explode(*_args, **_kwargs):
        raise OSError("vault unreadable")

    monkeypatch.setattr(vault_module, "load_model_api_route", _explode)

    assert describe_reviewer_route_unavailable(
        _BOOM,
        _env(tmp_path, backend="copilot"),
    ) == str(_BOOM)


def test_the_gate_stays_blocking_regardless_of_the_message() -> None:
    # Reverse assertion for the anti-fabrication line: this fix is cosmetic by
    # construction. If the blocking issue ever became conditional on the
    # backend, an unreviewed paper could pass on a copilot-only deployment.
    import inspect

    from argus_skill.verticals.research import academic_language_review as mod

    source = inspect.getsource(mod.generate_academic_language_review)
    call_site = source[source.index("model_review_unavailable"):][:400]
    assert "hard_gate=True" in call_site
    assert "describe_reviewer_route_unavailable" in call_site


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-AbCd1234EfGh5678IjKl",  # OpenAI project key
        "sk-ant-api03-xYz9876543210abcdef",  # Anthropic key
        "sk-abcdefghijkl123456",  # legacy flat OpenAI key
    ],
)
def test_provider_key_formats_never_reach_the_operator(
    tmp_path: Path,
    secret: str,
) -> None:
    # This message is written into paper/ACADEMIC_LANGUAGE_REVIEW.json, so a key
    # that survives it lands in the paper workspace. The local redactor used to
    # match `sk-[A-Za-z0-9]{12,}`, which stops at the first hyphen — exactly the
    # shape of every key OpenAI and Anthropic issue today.
    message = describe_reviewer_route_unavailable(
        RuntimeError(f"401 from https://api.example.invalid key={secret}"),
        _env(tmp_path, backend="copilot"),
    )

    assert secret not in message
