"""Tests for the gated, default-OFF engineer containment sandbox (Fix1).

Covers: the env gate, the writable allowlist's containment invariants, the
codex command construction (sandboxed vs legacy), the run_exec chokepoint that
converts un-sandboxed builder roles, the VCS-credential scrub, and the raw
subagent-spawn helpers. The default (gate OFF) must be byte-for-byte unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.core import sandbox

_ENV = "ARGUS_SKILL_ENGINEER_SANDBOX"


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv(_ENV, "workspace-write")


# ── gate ───────────────────────────────────────────────────────────────────
def test_gate_default_off(gate_off):
    assert sandbox.engineer_sandbox_mode() is None


@pytest.mark.parametrize("val,expected", [
    ("workspace-write", "workspace-write"),
    ("read-only", "read-only"),
    ("1", "workspace-write"),
    ("true", "workspace-write"),
    ("on", "workspace-write"),
    ("0", None),
    ("", None),
    ("garbage", None),
])
def test_gate_env_parsing(monkeypatch, val, expected):
    monkeypatch.setenv(_ENV, val)
    assert sandbox.engineer_sandbox_mode() == expected


# ── writable allowlist containment invariants ────────────────────────────────
def test_writable_roots_excludes_gate_brain_and_package():
    home = str(Path.home())
    roots = sandbox.writable_roots()
    # NEVER writable: the gate's brain, the package source, the codex config.
    for r in roots:
        assert not (r == home + "/.argus-skill" or r.startswith(home + "/.argus-skill/"))
        assert not (r == home + "/.codex" or r.startswith(home + "/.codex/"))
    forb = sandbox.forbidden_write_roots()
    assert home + "/.argus-skill" in forb
    assert home + "/.codex" in forb
    # the package root is forbidden
    import argus_skill
    pkg = str(Path(argus_skill.__file__).resolve().parent.parent)
    assert pkg in forb


def test_writable_roots_includes_research_caches():
    roots = sandbox.writable_roots()
    assert any(r.endswith("/.cache") for r in roots)   # pip / HF / torch
    assert any(r.endswith("/.kube") for r in roots)     # B200 kubectl token cache


def test_writable_roots_drops_candidate_under_forbidden(monkeypatch):
    # If the python env prefix were somehow under ~/.argus-skill, it must be dropped.
    home = str(Path.home())
    monkeypatch.setattr(sandbox.sys, "prefix", home + "/.argus-skill/venv")
    assert not any("/.argus-skill/" in r for r in sandbox.writable_roots())


# ── codex command construction ───────────────────────────────────────────────
def _codex_runner():
    return AgentCliRunner(agent_bin="codex")


def test_build_codex_command_sandboxed():
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(
            model="gpt-5.5", sandbox_mode="workspace-write",
            working_dir="/wd", add_dirs=["/home/u/.cache", "/tmp/x"],
        ),
    )
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert cmd[cmd.index("-s") + 1] == "workspace-write"
    assert cmd[cmd.index("-C") + 1] == "/wd"
    assert cmd.count("--add-dir") == 2
    assert "sandbox_workspace_write.network_access=true" in cmd


def test_build_codex_command_legacy_unchanged():
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(model="gpt-5.5", dangerous_yolo=True),
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "-s" not in cmd


# ── run_exec chokepoint (_apply_sandbox_policy) ──────────────────────────────
def test_chokepoint_noop_when_gate_off(gate_off):
    o = _codex_runner()._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True, working_dir="/wd"))
    assert o.dangerous_yolo is True and o.sandbox_mode is None


def test_chokepoint_converts_builder_when_gate_on(gate_on):
    o = _codex_runner()._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True, working_dir="/wd"))
    assert o.sandbox_mode == "workspace-write"
    assert o.dangerous_yolo is False and o.full_auto is False
    assert any(r.endswith("/.cache") for r in o.add_dirs)
    assert not any("/.argus-skill" in r for r in o.add_dirs)


def test_chokepoint_respects_explicit_mode(gate_on):
    o = _codex_runner()._apply_sandbox_policy(
        RunnerOptions(dangerous_yolo=True, sandbox_mode="read-only", working_dir="/wd")
    )
    assert o.sandbox_mode == "read-only"  # caller's explicit choice wins


def test_chokepoint_skips_non_builder_calls(gate_on):
    # A call with neither dangerous_yolo nor full_auto is not a builder role.
    o = _codex_runner()._apply_sandbox_policy(RunnerOptions(working_dir="/wd"))
    assert o.sandbox_mode is None


def test_chokepoint_skips_non_codex_backend(gate_on):
    from argus_skill.agent_cli.runner_backend import BACKEND_CLAUDE
    r = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)
    o = r._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True, working_dir="/wd"))
    assert o.sandbox_mode is None and o.dangerous_yolo is True


# ── env scrub ────────────────────────────────────────────────────────────────
def test_sandboxed_child_env_scrubs_vcs_creds(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/a.sock")
    monkeypatch.setenv("PATH", "/usr/bin")  # a normal var survives
    env = sandbox.sandboxed_child_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["PATH"] == "/usr/bin"


# ── raw subagent-spawn helpers ───────────────────────────────────────────────
def test_codex_sandbox_args_legacy_when_off(gate_off):
    assert sandbox.codex_sandbox_args(working_dir="/wd") == [
        "--dangerously-bypass-approvals-and-sandbox"
    ]
    assert sandbox.codex_sandbox_env() is None


def test_codex_sandbox_args_sandboxed_when_on(gate_on):
    args = sandbox.codex_sandbox_args(working_dir="/wd")
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert args[:4] == ["-s", "workspace-write", "-C", "/wd"]
    assert "--add-dir" in args
    assert sandbox.codex_sandbox_env()["PYTHONSAFEPATH"] == "1"


def test_no_hardcoded_bypass_left_in_subagent_spawns():
    """Every codex spawn must route through the gated policy. The only remaining
    literal bypass is the legacy default-OFF fallback in the runner/policy."""
    import argus_skill.tools.subagent._core as sub
    src = Path(sub.__file__).read_text()
    assert "--dangerously-bypass-approvals-and-sandbox" not in src
