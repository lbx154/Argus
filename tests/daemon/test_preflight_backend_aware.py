"""Backend-aware vault preflight (接入 copilot: 全 copilot 运行无需 Azure).

The vault preflight exists to catch a misconfigured Azure ``model_api`` route
before the daemon doom-loops on it. That rationale only applies to roles that
actually run on the codex/Azure backend — a role pinned to copilot/claude
authenticates through its own CLI and never touches the vault. So the preflight
must only probe routes whose role runs on codex; a fully copilot-backed run has
no Azure routes to probe and must start without ``ARGUS_SKILL_SKIP_VAULT_PREFLIGHT``.
"""
from __future__ import annotations

import os

import pytest

from argus.daemon import life_worker
from argus.daemon.life_worker import (
    _preflight_route_on_codex,
    required_codex_routes,
)

_BACKEND_ENVS = (
    "ARGUS_SKILL_RUNNER_BACKEND",
    "ARGUS_SKILL_REVIEWER_BACKEND",
    "ARGUS_SKILL_RUNNER_BIN",
    "ARGUS_SKILL_BACKEND_AUTH_MODE",
)


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch, tmp_path):
    for name in _BACKEND_ENVS:
        monkeypatch.delenv(name, raising=False)
    # Most cases exercise an explicitly configured Codex route and must not
    # depend on whether the developer machine happens to have Codex installed.
    # The dedicated fallback test replaces PATH and therefore still verifies
    # Codex-missing → Copilot behavior.
    fake_bin = tmp_path / "fake-codex-bin"
    fake_bin.mkdir()
    codex = fake_bin / "codex"
    codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    codex.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    # Isolate from the operator's persisted knob store without monkeypatching a
    # function that other modules may import lazily and retain after this test.
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))


def test_explicit_model_api_mode_probes_all_required_routes(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    assert required_codex_routes() == ["engineer", "reviewer", "text"]


def test_a_retired_role_backend_cannot_bring_back_a_codex_probe(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "codex")
    assert required_codex_routes() == []
    assert life_worker._worker_vault_preflight_routes("copilot") == []


def test_unknown_value_fails_closed_to_probe(monkeypatch) -> None:
    # A typo'd backend must NOT silently disable the safety probe.
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "coldex")
    assert _preflight_route_on_codex("engineer") is True
