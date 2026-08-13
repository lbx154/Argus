"""The cheap control-plane routes must not name an OpenAI model on a backend
that does not serve the OpenAI catalog.

Four routes — Manager front-door classify, bounded-DAG decomposition, ``/plan``
preview, and interactive prompt rewrite — want a *small* model rather than the
role's full-strength one. Each used to decide that with its own copy of
``backend in {"codex", "copilot", "pi"} -> "gpt-5.4-mini"``. The ``pi`` entry
was wrong: Pi is a provider-agnostic front whose catalog is whatever the
operator authenticated, so a Pi deployment on DeepSeek/Anthropic/vLLM asked its
provider for ``gpt-5.4-mini`` and every one of these four routes hard-failed —
even after the operator had correctly configured every model knob Argus
documents.

These pin the shared helper and all four call sites: codex/copilot keep their
exact previous ids, everything else falls back to the role model, and an
explicit knob always wins.
"""
from __future__ import annotations

import pytest

from argus_skill.core.knobs import resolve_cheap_route_model


@pytest.fixture(autouse=True)
def _no_persisted_knobs(monkeypatch, tmp_path) -> None:
    """Keep the operator's real ``~/.argus-skill`` switches out of these."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))


def _env(backend: str, **extra: str) -> dict[str, str]:
    return {"ARGUS_SKILL_LIFE_BACKEND": backend, **extra}


@pytest.mark.parametrize("backend", ["codex", "copilot"])
def test_openai_catalog_backends_keep_the_cheap_openai_id(backend: str) -> None:
    assert (
        resolve_cheap_route_model(
            knob="ARGUS_SKILL_FRONTDOOR_MODEL",
            catalog_default="gpt-5.4-mini",
            role="manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(backend, ARGUS_SKILL_MANAGER_MODEL="gpt-5.5"),
        )
        == "gpt-5.4-mini"
    )


@pytest.mark.parametrize("backend", ["pi", "claude", "opencode", "grok"])
def test_provider_agnostic_backends_fall_back_to_the_role_model(
    backend: str,
) -> None:
    """A DeepSeek/Anthropic/local Pi has no ``gpt-5.4-mini`` to sell."""
    assert (
        resolve_cheap_route_model(
            knob="ARGUS_SKILL_FRONTDOOR_MODEL",
            catalog_default="gpt-5.4-mini",
            role="manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(backend, ARGUS_SKILL_MANAGER_MODEL="deepseek-chat"),
        )
        == "deepseek-chat"
    )


@pytest.mark.parametrize(
    "backend",
    ["codex", "copilot", "pi", "claude", "grok"],
)
def test_explicit_knob_wins_on_every_backend(backend: str) -> None:
    assert (
        resolve_cheap_route_model(
            knob="ARGUS_SKILL_FRONTDOOR_MODEL",
            catalog_default="gpt-5.4-mini",
            role="manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(
                backend,
                ARGUS_SKILL_MANAGER_MODEL="deepseek-chat",
                ARGUS_SKILL_FRONTDOOR_MODEL="deepseek-reasoner",
            ),
        )
        == "deepseek-reasoner"
    )


@pytest.mark.parametrize("sentinel", ["auto", "inherit", "default", ""])
def test_auto_sentinels_are_not_treated_as_model_ids(sentinel: str) -> None:
    assert (
        resolve_cheap_route_model(
            knob="ARGUS_SKILL_FRONTDOOR_MODEL",
            catalog_default="gpt-5.4-mini",
            role="manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
            env=_env(
                "pi",
                ARGUS_SKILL_MANAGER_MODEL="deepseek-chat",
                ARGUS_SKILL_FRONTDOOR_MODEL=sentinel,
            ),
        )
        == "deepseek-chat"
    )


def test_front_door_classify_route_uses_the_shared_rule(monkeypatch) -> None:
    from argus_skill.core.knobs import resolve_manager_classify_model

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "deepseek-chat")
    monkeypatch.delenv("ARGUS_SKILL_FRONTDOOR_MODEL", raising=False)

    assert resolve_manager_classify_model() == "deepseek-chat"

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    assert resolve_manager_classify_model() == "gpt-5.6-sol"


def test_bounded_dag_route_uses_the_shared_rule(monkeypatch) -> None:
    from argus_skill.manager.dispatch import _bounded_dag_model

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_PLAN_MODEL", "deepseek-chat")
    monkeypatch.delenv("ARGUS_SKILL_BOUNDED_DAG_MODEL", raising=False)

    assert _bounded_dag_model() == "deepseek-chat"

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "copilot")
    assert _bounded_dag_model() == "gpt-5.4-mini"


def test_plan_preview_route_uses_the_shared_rule(monkeypatch) -> None:
    from argus_skill.webapi.manager_bridge import _plan_preview_model

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_PLAN_MODEL", "deepseek-chat")
    monkeypatch.delenv("ARGUS_SKILL_PLAN_PREVIEW_MODEL", raising=False)

    assert _plan_preview_model() == "deepseek-chat"

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    assert _plan_preview_model() == "gpt-5.4-mini"


def test_prompt_rewrite_route_keeps_its_own_catalog_default(monkeypatch) -> None:
    """Rewrite has always used ``gpt-5.5``, not the mini — keep it that way on
    the OpenAI-catalog backends while still fixing the provider-agnostic ones."""
    from argus_skill.webapi.manager_bridge import _rewrite_model_and_effort

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "gpt-5.6-sol")
    monkeypatch.delenv("ARGUS_SKILL_REWRITE_MODEL", raising=False)

    assert _rewrite_model_and_effort()[0] == "gpt-5.5"

    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "deepseek-chat")
    assert _rewrite_model_and_effort()[0] == "deepseek-chat"


def test_provider_knobs_normalize_to_a_single_catalog_name() -> None:
    """The cockpit can set these, so a provider/model pair typed into the box
    must be rejected rather than silently producing ``a/b/model``."""
    from argus_skill.core.knobs import normalize_cockpit_knob_value

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
