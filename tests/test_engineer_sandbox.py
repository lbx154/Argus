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
    # SECURITY: a containment-disabling value must NOT resolve to a "sandbox"
    # mode — it falls to None (honest legacy bypass), never a fake sandbox that
    # leaves ~/.argus-skill / the package / ~/.codex writable.
    ("danger-full-access", None),
    ("DANGER-FULL-ACCESS", None),
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
    assert any(r.endswith("/.triton") for r in roots)   # Triton JIT/autotune cache
    assert any(r.endswith("/.nv") for r in roots)       # NVIDIA ptxas/nvrtc cache


def test_writable_roots_never_grants_the_venv():
    """CRITICAL escape invariant: the active venv (sys.prefix) must NEVER be in
    the --add-dir allowlist and MUST be forbidden. Its site-packages auto-runs
    .pth / sitecustomize.py at the start of every python process, so write access
    there escapes the sandbox into the next un-sandboxed interpreter (the exact
    self-gate-rewrite vector this sandbox exists to stop)."""
    import sys as _sys
    prefix = str(Path(_sys.prefix))
    roots = sandbox.writable_roots()
    assert prefix not in roots
    assert not any(r == prefix or r.startswith(prefix + os.sep) for r in roots)
    assert prefix in sandbox.forbidden_write_roots()


def test_writable_roots_resolves_symlinked_candidate_into_venv(tmp_path, monkeypatch):
    """Cross-session escape: a sandboxed session can write ~/.cache, so it could
    repoint it at the venv via symlink. writable_roots() must realpath candidates
    so the symlinked ~/.cache resolves into the (forbidden) venv and is DROPPED —
    not handed back as a writable allowlist entry pointing at site-packages."""
    venv = tmp_path / "venv"
    (venv / "lib").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".cache").symlink_to(venv)          # attacker repoints ~/.cache → venv
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sandbox.sys, "prefix", str(venv))
    roots = sandbox.writable_roots()
    real_venv = os.path.realpath(str(venv))
    assert not any(r == real_venv or r.startswith(real_venv + os.sep) for r in roots)


def test_writable_roots_drops_candidate_under_forbidden(monkeypatch):
    # If the python env prefix were somehow under ~/.argus-skill, it must be dropped.
    home = str(Path.home())
    monkeypatch.setattr(sandbox.sys, "prefix", home + "/.argus-skill/venv")
    assert not any("/.argus-skill/" in r for r in sandbox.writable_roots())


def test_fail_closed_workdir_returns_real_nonsymlink_dir(tmp_path, monkeypatch):
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(fake_tmp))
    wd = sandbox.fail_closed_workdir()
    assert Path(wd).is_dir() and not Path(wd).is_symlink()
    assert os.path.realpath(wd).startswith(os.path.realpath(str(fake_tmp)))


def test_fail_closed_workdir_rejects_preplanted_symlink(tmp_path, monkeypatch):
    # The pid-derived scratch path is predictable and /tmp is engineer-writable, so
    # a prior sandboxed turn can pre-plant it as a symlink to the gate brain. The
    # rootless reviewer -C must NOT resolve there.
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(fake_tmp))
    gate_brain = tmp_path / "gate_brain"
    gate_brain.mkdir()
    (fake_tmp / f"argus-sandbox-scratch-{os.getpid()}").symlink_to(
        gate_brain, target_is_directory=True
    )
    wd = sandbox.fail_closed_workdir()
    assert not Path(wd).is_symlink()
    assert os.path.realpath(wd) != os.path.realpath(str(gate_brain))


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


def test_chokepoint_converts_all_codex_roles_when_on(gate_on):
    # Fix3: gate ON contains EVERY codex role, including ones that set neither
    # dangerous_yolo nor full_auto (they previously fell through to codex's
    # config default = danger-full-access, a silent un-sandboxed hole).
    o = _codex_runner()._apply_sandbox_policy(RunnerOptions(working_dir="/wd"))
    assert o.sandbox_mode == "workspace-write"


def test_chokepoint_fail_closed_when_no_workdir(gate_on):
    # Fix2: a sandboxed role with no working_dir must NOT be left to root its
    # writable workspace at the inherited cwd ("/"). The chokepoint pins a
    # contained -C, and the built command never emits -s without -C.
    o = _codex_runner()._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True))
    assert o.sandbox_mode == "workspace-write"
    assert o.working_dir and o.working_dir != "/" and os.path.isabs(o.working_dir)
    cmd = _codex_runner()._build_codex_command(resume_thread_id=None, options=o)
    assert "-C" in cmd
    c_root = cmd[cmd.index("-C") + 1]
    assert c_root not in ("/", "") and c_root == o.working_dir


def test_build_codex_command_never_rootless_sandbox():
    # Even called directly with no working_dir, -s must come with a contained -C.
    cmd = _codex_runner()._build_codex_command(
        resume_thread_id=None,
        options=RunnerOptions(sandbox_mode="workspace-write"),
    )
    assert "-s" in cmd and "-C" in cmd
    assert cmd[cmd.index("-C") + 1] not in ("/", "")


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
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-secret")
    monkeypatch.setenv("PATH", "/usr/bin")  # a normal var survives
    env = sandbox.sandboxed_child_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
    assert "COPILOT_GITHUB_TOKEN" not in env
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_isolated_workdir_wraps_any_backend_and_hides_vcs_credentials(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "worktree"
    (home / ".ssh").mkdir(parents=True)
    (home / ".config" / "gh").mkdir(parents=True)
    workdir.mkdir()
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    command = sandbox.isolated_workdir_command(
        ["copilot", "--version"],
        working_dir=workdir,
    )

    assert command[0] == "/usr/bin/bwrap"
    assert ["--bind", str(workdir), str(workdir)] == command[
        command.index("--bind") : command.index("--bind") + 3
    ]
    assert str(home / ".ssh") in command
    assert str(home / ".config" / "gh") in command
    assert command[-2:] == ["copilot", "--version"]


def test_isolated_workdir_fails_closed_without_bubblewrap(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="bubblewrap"):
        sandbox.isolated_workdir_command(["copilot"], working_dir=tmp_path)


def test_isolated_runner_scrubs_credentials_even_without_native_sandbox(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret")
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")

    env = runner._child_env(RunnerOptions(isolate_workdir=True))

    assert env is not None
    assert "GH_TOKEN" not in env
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GH_CONFIG_DIR"] == "/tmp/argus-no-gh-auth"


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


def test_no_raw_codex_spawn_bypasses_gate_anywhere():
    """Repo-wide invariant (not just one file): the literal dangerous bypass may
    appear ONLY in the runner/policy (the gated default-OFF fallback). Every
    other module that spawns codex (e.g. team/teammate_entry.py) must go through
    codex_sandbox_args/codex_sandbox_env, so enabling the gate contains them too.
    Guards against a future raw spawn re-opening the hole the PR closed."""
    import argus_skill
    pkg_root = Path(argus_skill.__file__).resolve().parent
    allowed = {"agent_cli/agent_cli_runner.py", "core/sandbox.py"}
    offenders = []
    for p in pkg_root.rglob("*.py"):
        rel = p.relative_to(pkg_root).as_posix()
        if rel in allowed:
            continue
        if "--dangerously-bypass-approvals-and-sandbox" in p.read_text():
            offenders.append(rel)
    assert offenders == [], f"raw codex bypass outside the gated chokepoint: {offenders}"


def test_teammate_research_spawn_is_gated():
    """team/teammate_entry.py's forced-research codex spawn must route through the
    sandbox helpers so it is contained when the gate is on (it was an un-gated
    danger-full-access spawn before this fix)."""
    import argus_skill.team.teammate_entry as te
    src = Path(te.__file__).read_text()
    assert "codex_sandbox_args" in src and "codex_sandbox_env" in src


# ── default-OFF inertness (must stay byte-for-byte legacy) ────────────────────
def test_off_path_inert_for_all_roles(gate_off):
    r = _codex_runner()
    # dangerous_yolo role unchanged
    o1 = r._apply_sandbox_policy(RunnerOptions(dangerous_yolo=True, working_dir="/wd"))
    assert o1.sandbox_mode is None and o1.dangerous_yolo is True
    # non-dangerous role also unchanged (Fix3 must not leak into the OFF path)
    o2 = r._apply_sandbox_policy(RunnerOptions(working_dir="/wd"))
    assert o2.sandbox_mode is None and o2.working_dir == "/wd"
    # legacy command still emits the bypass and no -s
    cmd = r._build_codex_command(resume_thread_id=None, options=o1)
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd and "-s" not in cmd
