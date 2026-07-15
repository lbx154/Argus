"""Regression: the Manager front-door must honour the operator's persisted
``/backend`` choice, not silently fall back to codex.

The bug: the web cockpit / REPL bridge resolves the operator's backend (e.g.
copilot, set via a persisted ``/backend`` knob in a PRIOR session) into
``args.backend`` but never exports ``ARGUS_SKILL_RUNNER_BACKEND`` into the
process env. ``_SkillLoopRunner`` built its default backend by reading that env
var ALONE, so with the env unset it built a **codex** backend and spawned
``codex exec`` against an Azure endpoint the copilot operator never configured —
``401 Reconnecting… n/100`` retry storm, the per-project front-door lock held
for minutes, and the cockpit showing "(couldn't reach Argus: fetch failed)".

These pin the precedence in the pure resolver so it can never regress without a
CLI/subprocess in the loop.
"""
from __future__ import annotations

import argparse

from argus_skill.apps import _runtime
from argus_skill.apps._runtime import _resolve_runner_backend_name


def _ns(backend: object) -> argparse.Namespace:
    return argparse.Namespace(backend=backend)


def test_falls_back_to_args_backend_when_env_unset() -> None:
    # The exact bug: copilot resolved into args.backend, env has no override.
    # Must return copilot — NOT the codex default.
    assert _resolve_runner_backend_name(_ns("copilot"), env={}) == "copilot"
    assert _resolve_runner_backend_name(_ns("claude"), env={}) == "claude"
    assert _resolve_runner_backend_name(_ns("codex"), env={}) == "codex"


def test_env_override_still_wins() -> None:
    # An explicit env var is a deliberate override and must beat args.backend,
    # so the daemon (which exports the env) keeps its exact prior behaviour.
    assert (
        _resolve_runner_backend_name(
            _ns("copilot"), env={"ARGUS_SKILL_RUNNER_BACKEND": "codex"}
        )
        == "codex"
    )
    assert (
        _resolve_runner_backend_name(
            _ns("codex"), env={"ARGUS_SKILL_RUNNER_BACKEND": "copilot"}
        )
        == "copilot"
    )


def test_memory_or_unknown_backend_defers_to_default() -> None:
    # memory / missing / unknown → None so AgentCliBackend applies its own
    # codex default (byte-for-byte the prior env-unset behaviour there).
    assert _resolve_runner_backend_name(_ns("memory"), env={}) is None
    assert _resolve_runner_backend_name(_ns(None), env={}) is None
    assert _resolve_runner_backend_name(argparse.Namespace(), env={}) is None


def test_blank_env_value_is_ignored() -> None:
    # A whitespace-only env var must not shadow a real args.backend.
    assert (
        _resolve_runner_backend_name(
            _ns("copilot"), env={"ARGUS_SKILL_RUNNER_BACKEND": "   "}
        )
        == "copilot"
    )


def test_persisted_role_backend_overrides_resolved_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_ENGINEER_BACKEND": "claude"},
    )

    assert (
        _runtime._resolve_role_runner_backend_name(
            "engineer", "copilot", env={},
        )
        == "claude"
    )


def test_explicit_shared_env_overrides_persisted_role_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_ENGINEER_BACKEND": "codex"},
    )

    assert (
        _runtime._resolve_role_runner_backend_name(
            "engineer",
            "copilot",
            env={"ARGUS_SKILL_RUNNER_BACKEND": "copilot"},
        )
        == "copilot"
    )
