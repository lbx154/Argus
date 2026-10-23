"""Tests for the access policy around the agent CLIs.

Covers: the global access policy, the roots no role may write, the codex
command construction (sandboxed vs legacy), the VCS-credential scrub, and the
raw subagent-spawn helpers.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core import sandbox


@pytest.fixture
def safe_mode(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")


# ── policy ─────────────────────────────────────────────────────────────────
def test_default_policy_grants_every_backend_full_access(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)
    for backend in ("codex", "claude", "copilot", "cursor", "opencode", "pi"):
        runner = AgentCliRunner(agent_bin=backend, backend=backend)
        options = runner._apply_sandbox_policy(
            RunnerOptions(sandbox_mode="read-only", isolate_workdir=True)
        )
        assert options.sandbox_mode is None
        assert options.isolate_workdir is False
        assert options.dangerous_yolo is True


# ── roots no role may write ──────────────────────────────────────────────────
def test_forbidden_roots_include_gate_brain_and_package():
    home = Path.home()
    forbidden = {Path(root) for root in sandbox.forbidden_write_roots()}
    assert home / ".argus-skill" in forbidden
    assert home / ".codex" in forbidden
    # the package root is forbidden
    import argus
    package_root = Path(argus.__file__).resolve().parent.parent
    assert package_root in forbidden


def test_forbidden_roots_include_user_site_and_local_bin():
    """2026-09-05 escape: with the system site unwritable, pip inside the
    maintenance worktree silently fell back to a *user* install, rewriting
    ~/.local/bin/argus and planting an editable .pth in the user
    site-packages — both auto-load into the next un-sandboxed interpreter.
    Both roots must be forbidden. ~/.local/lib
    covers the user site of EVERY interpreter version — the incident pip ran
    under the system 3.11, not this venv's 3.12 — while the per-interpreter
    user site stays forbidden for a PYTHONUSERBASE outside home. (The
    worktree itself stays writable: it is the -C / bwrap bind root, which
    forbidden_write_roots never filters.)"""
    import site
    home = Path.home()
    forbidden = {Path(root) for root in sandbox.forbidden_write_roots()}
    assert home / ".local" / "lib" in forbidden
    assert home / ".local" / "bin" in forbidden
    assert Path(site.getusersitepackages()) in forbidden


def test_forbidden_user_site_follows_substituted_home(tmp_path, monkeypatch):
    """site.getusersitepackages() is resolved once against the startup HOME;
    when a test substitutes Path.home(), the forbidden user-site root must
    move with the other home-derived roots instead of pointing at the real
    home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))
    forbidden = [Path(root) for root in sandbox.forbidden_write_roots()]
    assert home / ".local" / "lib" in forbidden
    assert home / ".local" / "bin" in forbidden
    assert any(
        root.name == "site-packages" and root.is_relative_to(home)
        for root in forbidden
    )


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


def test_build_codex_command_disables_interactive_notify_hook_per_turn():
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(model="gpt-5.5", dangerous_yolo=True),
    )

    pairs = list(zip(cmd, cmd[1:]))
    assert ("-c", "notify=[]") in pairs
    # One empty ``write_stdin`` may wait as long as Codex accepts (TOML's i64).
    assert ("-c", f"background_terminal_max_timeout={2**63 - 1}") in pairs


def test_build_codex_no_tools_keeps_provider_config_but_skips_tool_startup():
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(
            model="deepseek-v4-flash",
            disable_tools=True,
            sandbox_mode="read-only",
        ),
    )

    pairs = list(zip(cmd, cmd[1:]))
    assert "--ignore-user-config" not in cmd
    assert "--ignore-rules" in cmd
    assert ("-c", "mcp_servers={}") in pairs
    assert ("-c", "plugins={}") in pairs
    assert ("-c", "features.js_repl=false") in pairs
    assert ("-c", 'web_search="disabled"') in pairs
    assert cmd[cmd.index("-m") + 1] == "deepseek-v4-flash"


def test_build_codex_command_legacy_unchanged():
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(model="gpt-5.5", dangerous_yolo=True),
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "-s" not in cmd


# ── run_exec chokepoint (_apply_sandbox_policy) ──────────────────────────────
def test_chokepoint_allows_per_invocation_safe_mode():
    o = _codex_runner()._apply_sandbox_policy(
        RunnerOptions(
            dangerous_yolo=True,
            sandbox_mode="read-only",
            force_safe_mode=True,
            working_dir="/wd",
        )
    )
    assert o.dangerous_yolo is True
    assert o.sandbox_mode == "read-only"


# ── env scrub ────────────────────────────────────────────────────────────────
def test_sandboxed_child_env_scrubs_vcs_creds(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/a.sock")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-secret")
    monkeypatch.setenv("PATH", "/usr/bin")  # a normal var survives
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    env = sandbox.sandboxed_child_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
    assert "COPILOT_GITHUB_TOKEN" not in env
    # The agent's shell runs the project's own tests; safe-path breaks their
    # subprocess imports, so it stays on Argus's own interpreters only.
    assert "PYTHONSAFEPATH" not in env
    assert env["PATH"] == "/usr/bin"


def test_sandboxed_child_env_pins_pip_user_off(monkeypatch):
    """pip's silent user-install fallback is the 2026-09-05 escape; PIP_USER=0
    makes an unwritable-site install fail loudly (Errno 13) instead. An
    inherited opt-in must not survive."""
    monkeypatch.setenv("PIP_USER", "1")
    env = sandbox.sandboxed_child_env()
    assert env["PIP_USER"] == "0"


def test_pip_user_pinned_off_on_the_yolo_inherit_path():
    """The maintenance engineer runs dangerous_yolo by default
    (apps/_runtime_execute.py): _child_env returns None and the codex child
    inherits the parent env untouched, so sandboxed_child_env() never runs.
    PIP_USER=0 therefore rides the parent env via
    configure_framework_python_env, which the daemon life worker
    (_rf_bootstrap_environment) and the CLI main both run before spawning any
    child shell."""
    from argus.core.runtime_env import configure_framework_python_env

    runner = _codex_runner()
    options = runner._apply_sandbox_policy(
        RunnerOptions(dangerous_yolo=True, working_dir="/wd")
    )
    assert runner._child_env(options) is None  # yolo child inherits parent env
    parent = configure_framework_python_env({"PATH": "/usr/bin", "PIP_USER": "1"})
    assert parent["PIP_USER"] == "0"


def test_isolated_runner_scrubs_credentials_even_without_native_sandbox(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "secret")
    monkeypatch.setenv("KUBECONFIG", "/secret/kubeconfig")
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")

    env = runner._child_env(RunnerOptions(isolate_workdir=True))

    assert env is not None
    assert "GH_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SESSION_TOKEN" not in env
    assert "KUBECONFIG" not in env
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert Path(env["GH_CONFIG_DIR"]) == Path(tempfile.gettempdir()) / "argus-no-gh-auth"


def test_isolated_copilot_disables_builtin_mcp_and_custom_instructions() -> None:
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")

    command = runner._build_copilot_command(
        resume_thread_id=None,
        options=RunnerOptions(isolate_workdir=True),
    )

    assert "--disable-builtin-mcps" in command
    assert "--no-custom-instructions" in command


# ── raw subagent-spawn helpers ───────────────────────────────────────────────
def test_no_raw_codex_spawn_bypasses_gate_anywhere():
    """Repo-wide invariant (not just one file): the literal dangerous bypass may
    appear ONLY in the runner/policy (the gated default-OFF fallback). Every
    other module that spawns codex must go through that chokepoint, so enabling
    the gate contains it too. Guards against a future raw spawn re-opening the
    hole the PR closed.

    ``agent_cli/_sandbox_commands.py`` holds the codex command builders +
    ``_apply_sandbox_policy`` gate that used to live inline in
    ``agent_cli_runner.py``; it is the same single chokepoint, just split into
    its own module, so it is allowed alongside the other two."""
    import argus
    pkg_root = Path(argus.__file__).resolve().parent
    allowed = {
        "agent_cli/agent_cli_runner.py",
        "agent_cli/_sandbox_commands.py",
        "core/sandbox.py",
    }
    offenders = []
    for p in pkg_root.rglob("*.py"):
        rel = p.relative_to(pkg_root).as_posix()
        if rel in allowed:
            continue
        if "--dangerously-bypass-approvals-and-sandbox" in p.read_text(
            encoding="utf-8"
        ):
            offenders.append(rel)
    assert offenders == [], f"raw codex bypass outside the gated chokepoint: {offenders}"


# ── default-OFF inertness (must stay byte-for-byte legacy) ────────────────────
def test_safe_mode_keeps_each_role_s_own_access(safe_mode):
    r = _codex_runner()
    o1 = r._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True, working_dir="/wd"))
    assert o1.sandbox_mode is None and o1.dangerous_yolo is True
    o2 = r._apply_sandbox_policy(RunnerOptions(full_auto=True, working_dir="/wd"))
    assert o2.sandbox_mode is None and o2.full_auto is True and o2.working_dir == "/wd"
    cmd = r._build_codex_command(resume_thread_id=None, options=o1)
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd and "-s" not in cmd


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX executable wrapper")
def test_copilot_wrapper_exposes_real_nvm_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    wrapper = home / "bin" / "copilot"
    real = (
        home / ".nvm" / "versions" / "node" / "v22" / "lib" / "node_modules"
        / "@github" / "copilot" / "node_modules" / "@github"
        / "copilot-linux-x64" / "copilot"
    )
    wrapper.parent.mkdir(parents=True)
    real.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    real.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))

    assert sandbox._backend_support_executables(wrapper) == [real.resolve()]


def test_a_sandboxed_engineer_may_read_the_skill_libraries_its_prompt_names(tmp_path):
    """2026-10-20: a safe-mode Engineer was denied the Skill libraries its own
    prompt told it to search, the packaged ones included."""
    from argus import SkillLoop, SkillLoopConfig
    from argus.skills.builtins import builtin_skill_source_path

    handoffs = str(tmp_path / "handoffs")
    loop = SkillLoop(
        skills_dir=tmp_path / "skills", engineer_runner=object(),
        config=SkillLoopConfig(add_dirs=[handoffs]),
    )

    roots = [str(path) for path in loop.engineer_mission.libraries().library_roots]
    add_dirs = loop.supervised.engineer_config.add_dirs
    assert add_dirs == list(dict.fromkeys([handoffs, *roots]))
    assert str(builtin_skill_source_path().resolve()) in add_dirs
