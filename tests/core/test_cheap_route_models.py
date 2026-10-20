"""The light model must not name an OpenAI model on a backend that does not
serve the OpenAI catalog.

Every light route (front-door classify, ``/plan`` preview, prompt rewrite,
map copy, reflection) runs on ``ARGUS_SKILL_LIGHT_MODEL``. Pi has a
provider-defined catalog, while Codex may authenticate through a ChatGPT
account with a restricted catalog; neither may receive an assumed OpenAI id.
Copilot gets the light default, everything else follows the Manager model, and
an explicit value always wins.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.core.knobs import LIGHT_MODEL_DEFAULT, resolve_light_model, resolve_role_model


@pytest.fixture(autouse=True)
def _no_persisted_knobs(monkeypatch, tmp_path) -> None:
    """Keep the operator's real runtime and Codex provider out of these."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("ARGUS_SKILL_CODEX_CONFIG", raising=False)


def _env(backend: str, **extra: str) -> dict[str, str]:
    return {
        "ARGUS_SKILL_LIFE_BACKEND": backend,
        "CODEX_HOME": os.environ["CODEX_HOME"],
        **extra,
    }


@pytest.mark.parametrize("backend", ["pi", "claude", "opencode", "grok"])
def test_provider_backend_without_model_uses_its_native_default(backend: str) -> None:
    assert (
        resolve_role_model(
            "manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(backend),
        )
        == ""
    )


@pytest.mark.parametrize("backend", ["codex", "copilot"])
def test_openai_backend_without_model_keeps_argus_default(backend: str) -> None:
    assert (
        resolve_role_model(
            "manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(backend),
        )
        == "gpt-6.1-sol"
    )


def test_custom_codex_provider_uses_its_configured_default_model() -> None:
    """A Codex CLI pointed at DeepSeek must not receive an OpenAI-only id."""
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
model_provider = "deepseek"
model = "deepseek-v4-flash"

[model_providers.deepseek]
base_url = "https://api.deepseek.invalid/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )
    env = _env("codex")

    assert resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        env=env,
    ) == ""
    assert resolve_light_model(env=env) == ""


def test_auto_model_override_uses_backend_default() -> None:
    assert (
        resolve_role_model(
            "manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env("claude", ARGUS_SKILL_MANAGER_MODEL="auto"),
        )
        == ""
    )


def test_role_model_can_use_the_actual_fallback_backend() -> None:
    assert (
        resolve_role_model(
            "manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            backend="claude",
            env=_env("codex"),
        )
        == ""
    )


def test_light_model_uses_the_actual_runtime_backend() -> None:
    assert resolve_light_model(backend="claude", env=_env("codex")) == ""


def test_explicit_shared_model_beats_a_persisted_light_model(monkeypatch) -> None:
    from argus.core import knob_store

    monkeypatch.setattr(
        knob_store, "read_persisted_knobs",
        lambda: {"ARGUS_SKILL_LIGHT_MODEL": "deepseek-official/deepseek-v4-flash"},
    )
    env = _env("copilot", ARGUS_SKILL_MODEL="gpt-5.6-sol")

    assert resolve_light_model(backend="copilot", env=env) == "gpt-5.6-sol"


def test_claude_command_omits_openai_model_when_unconfigured() -> None:
    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus.agent_cli.runner_backend import BACKEND_CLAUDE

    model = resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        env=_env("claude"),
    )
    command = AgentCliRunner(
        "claude",
        backend=BACKEND_CLAUDE,
    )._build_command(
        resume_thread_id=None,
        options=RunnerOptions(model=model),
    )

    assert "--model" not in command
    assert "gpt-5.5" not in command


def test_copilot_gets_the_light_default() -> None:
    env = _env("copilot", ARGUS_SKILL_MANAGER_MODEL="gpt-5.5")
    assert resolve_light_model(env=env) == LIGHT_MODEL_DEFAULT
    env["ARGUS_SKILL_MANAGER_MODEL"] = "auto"
    assert resolve_light_model(env=env) == resolve_role_model(
        "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", env=env,
    )


@pytest.mark.parametrize("backend", ["codex", "pi", "claude", "opencode", "grok"])
def test_provider_agnostic_backends_fall_back_to_the_manager_model(backend: str) -> None:
    """A DeepSeek/Anthropic/local Pi has no OpenAI id to sell."""
    env = _env(backend, ARGUS_SKILL_MANAGER_MODEL="deepseek-chat")
    assert resolve_light_model(env=env) == "deepseek-chat"


@pytest.mark.parametrize("backend", ["codex", "copilot", "pi", "claude", "grok"])
def test_explicit_light_model_wins_on_every_backend(backend: str) -> None:
    env = _env(backend, ARGUS_SKILL_MANAGER_MODEL="deepseek-chat", ARGUS_SKILL_LIGHT_MODEL="deepseek-reasoner")
    assert resolve_light_model(env=env) == "deepseek-reasoner"


@pytest.mark.parametrize("sentinel", ["auto", "inherit", "default", ""])
def test_auto_sentinels_are_not_treated_as_model_ids(sentinel: str) -> None:
    env = _env("pi", ARGUS_SKILL_MANAGER_MODEL="deepseek-chat", ARGUS_SKILL_LIGHT_MODEL=sentinel)
    assert resolve_light_model(env=env) == "deepseek-chat"


def test_light_effort_defaults_low_and_old_route_knobs_are_ignored() -> None:
    from argus.core.knobs import resolve_light_effort

    old = {"ARGUS_SKILL_FRONTDOOR_MODEL": "old", "ARGUS_SKILL_REWRITE_REASONING_EFFORT": "high"}
    assert resolve_light_effort(env=old) == "low"
    assert resolve_light_effort(env={"ARGUS_SKILL_LIGHT_REASONING_EFFORT": "medium"}) == "medium"
    env = _env("pi", ARGUS_SKILL_MANAGER_MODEL="deepseek-chat", **old)
    assert resolve_light_model(env=env) == "deepseek-chat"


def test_provider_knobs_normalize_to_a_single_catalog_name() -> None:
    """The cockpit can set these, so a provider/model pair typed into the box
    must be rejected rather than silently producing ``a/b/model``."""
    from argus.core.knobs import normalize_cockpit_knob_value

    assert (
        normalize_cockpit_knob_value("ARGUS_SKILL_PI_PROVIDER", " deepseek ")
        == "deepseek"
    )
    assert (
        normalize_cockpit_knob_value(
            "ARGUS_SKILL_OPENCODE_PROVIDER", "copilot-forward"
        )
        == "copilot-forward"
    )
    with pytest.raises(ValueError, match="single provider id"):
        normalize_cockpit_knob_value(
            "ARGUS_SKILL_PI_PROVIDER", "deepseek/deepseek-chat"
        )
