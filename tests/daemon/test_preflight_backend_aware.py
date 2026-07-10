"""Backend-aware vault preflight (接入 copilot: 全 copilot 运行无需 Azure).

The vault preflight exists to catch a misconfigured Azure ``model_api`` route
before the daemon doom-loops on it. That rationale only applies to roles that
actually run on the codex/Azure backend — a role pinned to copilot/claude
authenticates through its own CLI and never touches the vault. So the preflight
must only probe routes whose role runs on codex; a fully copilot-backed run has
no Azure routes to probe and must start without ``ARGUS_SKILL_SKIP_VAULT_PREFLIGHT``.
"""
from __future__ import annotations

import pytest

from argus_skill.daemon.life_worker import (
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
)


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch):
    for name in _BACKEND_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    # Isolate from the operator's persisted knob store (config.json): the
    # resolver now consults it, so tests must not depend on the real file.
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs", lambda: {}
    )


def test_default_probes_all_required_routes() -> None:
    # No overrides → every required route is on codex → probe them all (unchanged).
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
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "copilot")
    assert required_codex_routes() == ["reviewer", "text"]


def test_per_role_override_beats_runner_default(monkeypatch) -> None:
    # runner default copilot, but reviewer explicitly forced back to codex.
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "codex")
    assert required_codex_routes() == ["reviewer"]


def test_claude_backend_also_skips(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    assert required_codex_routes() == []


def test_unknown_value_fails_closed_to_probe(monkeypatch) -> None:
    # A typo'd backend must NOT silently disable the safety probe.
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "coldex")
    assert _preflight_route_on_codex("engineer") is True


def test_text_route_follows_runner_default(monkeypatch) -> None:
    # 'text' has no dedicated backend env; it tracks the default runner backend.
    assert _preflight_route_on_codex("text") is True
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    assert _preflight_route_on_codex("text") is False
