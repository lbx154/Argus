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

from argus_skill.daemon import life_worker
from argus_skill.daemon.life_worker import (
    _effective_runner_backend,
    _preflight_route_on_codex,
    required_codex_routes,
)

_BACKEND_ENVS = (
    "ARGUS_SKILL_RUNNER_BACKEND",
    "ARGUS_SKILL_ENGINEER_BACKEND",
    "ARGUS_SKILL_REVIEWER_BACKEND",
    "ARGUS_SKILL_PLANNER_BACKEND",
    "ARGUS_SKILL_MANAGER_BACKEND",
    "ARGUS_SKILL_CURATOR_BACKEND",
    "ARGUS_SKILL_RUNNER_BIN",
    "ARGUS_SKILL_ENGINEER_RUNNER_BIN",
    "ARGUS_SKILL_REVIEWER_RUNNER_BIN",
    "ARGUS_SKILL_PLANNER_RUNNER_BIN",
    "ARGUS_SKILL_MANAGER_RUNNER_BIN",
    "ARGUS_SKILL_CURATOR_RUNNER_BIN",
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


def test_default_codex_subscription_skips_model_api_routes() -> None:
    assert required_codex_routes() == []


def test_explicit_model_api_mode_probes_all_required_routes(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    assert required_codex_routes() == ["engineer", "reviewer", "text"]


def test_global_copilot_skips_all_routes(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    assert required_codex_routes() == []  # empty → daemon skips the preflight


def test_persisted_copilot_skips_without_env(monkeypatch) -> None:
    # A copilot choice persisted via ``/backend`` (config.json) MUST be honoured
    # even with no shell env — a non-interactive launcher (web autostart, tmux
    # exec, cron) never sources .bashrc, so the interactive-only export is
    # invisible and the daemon would otherwise wrongly probe the codex vault.
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_RUNNER_BACKEND": "copilot"},
    )
    assert required_codex_routes() == []


def test_mixed_probes_only_codex_roles(monkeypatch) -> None:
    # engineer on copilot, reviewer left on codex default → probe only reviewer+text.
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "copilot")
    assert required_codex_routes() == ["reviewer", "text"]


def test_per_role_override_beats_runner_default(monkeypatch) -> None:
    # runner default copilot, but reviewer explicitly forced back to codex.
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "codex")
    assert required_codex_routes() == ["reviewer"]


def test_claude_backend_also_skips(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    assert required_codex_routes() == []


def test_pi_backend_also_skips(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "pi")
    assert required_codex_routes() == []


def test_opencode_backend_also_skips(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "opencode")
    assert required_codex_routes() == []


def test_unknown_value_fails_closed_to_probe(monkeypatch) -> None:
    # A typo'd backend must NOT silently disable the safety probe.
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "coldex")
    assert _preflight_route_on_codex("engineer") is True


def test_missing_default_codex_uses_copilot_without_vault_probe(
    tmp_path,
    monkeypatch,
) -> None:
    copilot = tmp_path / "copilot"
    copilot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    copilot.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))

    assert required_codex_routes() == []


def test_effective_runner_backend_uses_instantiated_fallback() -> None:
    from types import SimpleNamespace

    from argus_skill.adapters.agent_cli_backend import AgentCliBackend

    runner = SimpleNamespace(
        backend=AgentCliBackend(backend="copilot", runner_bin="copilot")
    )

    assert _effective_runner_backend(runner, "codex") == "copilot"


def test_text_route_follows_runner_default(monkeypatch) -> None:
    # 'text' has no dedicated backend env; it tracks the default runner backend.
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    assert _preflight_route_on_codex("text") is True
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    assert _preflight_route_on_codex("text") is False


def test_copilot_worker_still_preflights_codex_role(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "model_api")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "codex")

    assert life_worker._worker_vault_preflight_routes("copilot") == ["engineer"]
    assert life_worker._worker_vault_preflight_routes("memory") == []
