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


def test_explicit_shared_model_beats_a_persisted_light_model(monkeypatch) -> None:
    from argus.core import knob_store

    monkeypatch.setattr(
        knob_store, "read_persisted_knobs",
        lambda: {"ARGUS_SKILL_LIGHT_MODEL": "deepseek-official/deepseek-v4-flash"},
    )
    env = _env("copilot", ARGUS_SKILL_MODEL="gpt-5.6-sol")

    assert resolve_light_model(backend="copilot", env=env) == "gpt-5.6-sol"


def test_copilot_gets_the_light_default() -> None:
    env = _env("copilot", ARGUS_SKILL_MANAGER_MODEL="gpt-5.5")
    assert resolve_light_model(env=env) == LIGHT_MODEL_DEFAULT
    env["ARGUS_SKILL_MANAGER_MODEL"] = "auto"
    assert resolve_light_model(env=env) == resolve_role_model(
        "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", env=env,
    )


@pytest.mark.parametrize("backend", ["codex", "copilot", "pi", "claude", "grok"])
def test_explicit_light_model_wins_on_every_backend(backend: str) -> None:
    env = _env(backend, ARGUS_SKILL_MANAGER_MODEL="deepseek-chat", ARGUS_SKILL_LIGHT_MODEL="deepseek-reasoner")
    assert resolve_light_model(env=env) == "deepseek-reasoner"


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
