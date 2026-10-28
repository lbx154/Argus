"""Agents see only what their call is given, by construction.

Two tenants share one OS account, as on a trial host, and each has two
projects. Every project's agent tries the escapes a path check cannot stop
(PR #302's review): a path assembled at run time, Python, a script it writes
and then runs, a symlink, a recursive search of the disk. Nothing of another
project or tenant, of the Argus runtime or of the portal key comes back; its
own work does.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core import sandbox

TENANTS = ("tenant-a", "tenant-b")
PROJECTS = ("alpha", "beta")


@dataclass(frozen=True)
class Project:
    tenant: str
    name: str
    root: Path  # tenants/<tenant>: what the deployment account owns for it

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def state(self) -> Path:  # ARGUS_SKILL_HOME
        return self.root / "state"

    @property
    def workdir(self) -> Path:
        return self.root / "work" / self.name

    @property
    def handoff(self) -> Path:
        return self.state / "projects" / self.name / "handoffs" / "task-1"

    @property
    def files(self) -> dict[str, Path]:
        return {
            "workspace": self.workdir / "results" / "secret.txt",
            "handoff": self.handoff / "mission.json",
            "state": self.state / "projects" / self.name / "session.json",
            "config": self.state / "config.json",
            "pi-transcript": self.state / "pi-sessions" / f"{self.name}.jsonl",
            "codex-transcript": self.home / ".codex" / "sessions" / f"{self.name}.jsonl",
        }

    def marker(self, label: str) -> str:
        return f"PRIVATE {self.tenant} {self.name} {label}"


@pytest.fixture
def world(tmp_path) -> dict[tuple[str, str], Project]:
    projects = {}
    for tenant in TENANTS:
        for name in PROJECTS:
            project = Project(tenant, name, tmp_path / "tenants" / tenant)
            for label, path in project.files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as handle:
                    handle.write(project.marker(label) + "\n")
            projects[tenant, name] = project
    (tmp_path / "portal").mkdir()
    (tmp_path / "portal" / "key").write_text("PRIVATE portal key\n")
    return projects


def _run(project: Project, script: str, monkeypatch, *, writable: bool = True):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.state))
    env = {**os.environ, "HOME": str(project.home)}
    command = sandbox.isolated_workdir_command(
        ["/bin/sh", "-c", script], backend="codex", working_dir=project.workdir,
        written=[project.handoff], writable=writable, env=env,
    )
    return subprocess.run(command, env=env, cwd=project.workdir, capture_output=True, text=True, timeout=120)


def test_an_agent_writes_its_workdir_and_its_own_home_copy_only(world, monkeypatch, host_sandbox):
    project, other = world["tenant-a", "alpha"], world["tenant-b", "beta"]
    result = _run(project, "\n".join([
        "echo made > results/new.txt",
        f"echo nope > {other.workdir}/results/new.txt",
        "echo mine > $HOME/.profile",
        "echo note >> " + str(project.handoff / "mission.json"),
    ]), monkeypatch)
    assert (project.workdir / "results" / "new.txt").read_text() == "made\n", result.stderr
    assert not (other.workdir / "results" / "new.txt").exists()
    assert not (project.home / ".profile").exists()
    assert sandbox.private_path(project.home / ".profile", project.workdir).read_text() == "mine\n"
    assert (project.handoff / "mission.json").read_text().endswith("note\n")


def test_a_linked_worktree_reads_its_history_but_keeps_its_pointer(tmp_path, monkeypatch, host_sandbox):
    repo, tree = tmp_path / "repo", tmp_path / "tree"
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=a"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "first"], check=True)
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(tree)], check=True)
    pointer = (tree / ".git").read_text()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "state"))
    command = sandbox.isolated_workdir_command(
        ["/bin/sh", "-c", "git log --format=%s; echo 'gitdir: /elsewhere' > .git"],
        backend="codex", working_dir=tree,
    )
    result = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=60)
    assert "first" in result.stdout
    assert (tree / ".git").read_text() == pointer


def test_the_runner_starts_every_agent_cli_in_the_sandbox(world, tmp_path, monkeypatch, host_sandbox):
    project, other = world["tenant-a", "alpha"], world["tenant-b", "beta"]
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    attempts = "; ".join(f"cat {path} 2>&1" for path in (project.files["workspace"], other.files["workspace"]))
    fake.write_text(
        f"#!{sys.executable}\nimport json, subprocess, sys\nsys.stdin.read()\n"
        f"seen = subprocess.run({attempts!r}, shell=True, capture_output=True, text=True).stdout\n"
        "for event in ({'type': 'thread.started', 'thread_id': 't'},\n"
        "              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': seen}},\n"
        "              {'type': 'turn.completed', 'usage': {}}):\n"
        "    print(json.dumps(event), flush=True)\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.state))
    monkeypatch.setenv("HOME", str(project.home))

    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(dangerous_yolo=True, working_dir=str(project.workdir)),
    )

    assert result.command[0] == host_sandbox
    assert result.turn_completed, result.fatal_error
    assert project.marker("workspace") in result.last_agent_message
    assert other.marker("workspace") not in result.last_agent_message


def _detaching_cli(path: Path, seconds: float) -> Path:
    """A fake CLI that starts a job detached, as the Engineer's long-run rule
    teaches, says so and ends its turn while the job still runs."""
    job = f"sleep {seconds}; echo 0 > results/exit_code"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\nimport json, subprocess, sys\nsys.stdin.read()\n"
        f"subprocess.run({f'setsid nohup sh -c {job!r} > results/log 2>&1 < /dev/null &'!r}, shell=True)\n"
        "for event in ({'type': 'thread.started', 'thread_id': 't'},\n"
        "              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'started detached'}},\n"
        "              {'type': 'turn.completed', 'usage': {}}):\n"
        "    print(json.dumps(event), flush=True)\n"
    )
    path.chmod(0o755)
    return path


def test_a_job_started_detached_finishes_before_the_turn_ends(world, tmp_path, monkeypatch, host_sandbox):
    """The sandbox used to end with the CLI, and the kernel killed the job
    with it: the next round found no exit flag (2026-10-27 trial)."""
    project = world["tenant-a", "alpha"]
    fake = _detaching_cli(tmp_path / "bin" / "codex", 2)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.state))
    monkeypatch.setenv("HOME", str(project.home))

    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(dangerous_yolo=True, working_dir=str(project.workdir)),
    )

    assert result.turn_completed, result.fatal_error
    assert result.last_agent_message == "started detached"
    assert (project.workdir / "results" / "exit_code").read_text() == "0\n"
    assert any(line.startswith(sandbox.SANDBOX_WAIT_MARKER) and "sleep 2" in line for line in result.stderr_lines)


def test_the_sandbox_exits_with_the_cli_code_once_its_jobs_end(world, monkeypatch, host_sandbox):
    project = world["tenant-a", "alpha"]
    result = _run(project, "(sleep 1; echo late > results/late.txt) & exit 3", monkeypatch)
    assert result.returncode == 3
    assert (project.workdir / "results" / "late.txt").read_text() == "late\n"


def test_a_turn_stopped_while_its_job_runs_names_the_job(world, tmp_path, monkeypatch, host_sandbox):
    """Stopping the turn stops its jobs too, a decision someone made; the
    stop reason names what was stopped, for the next round to relaunch."""
    project = world["tenant-a", "alpha"]
    fake = _detaching_cli(tmp_path / "bin" / "codex", 30)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.state))
    monkeypatch.setenv("HOME", str(project.home))

    def decide(snapshot):
        return "restart: the Manager steered" if snapshot.running_tool else "continue"

    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(
            dangerous_yolo=True, working_dir=str(project.workdir),
            watchdog_soft_idle_seconds=1, inactivity_callback=decide,
        ),
    )

    assert result.turn_failed
    assert "running tool:" in result.fatal_error and "sleep 30" in result.fatal_error
    assert not (project.workdir / "results" / "exit_code").exists()


def test_without_a_sandbox_an_isolated_call_is_refused_and_others_run(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho '{\"type\": \"turn.completed\", \"usage\": {}}'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")  # the only mode that isolates
    runner = AgentCliRunner(agent_bin=str(fake), backend="codex")
    refused = runner.run_exec(prompt="x", resume_thread_id=None,
                              options=RunnerOptions(isolate_workdir=True, working_dir=str(tmp_path)))
    assert refused.fatal_error.startswith("agent sandbox unavailable")
    ran = runner.run_exec(prompt="x", resume_thread_id=None, options=RunnerOptions(working_dir=str(tmp_path)))
    assert ran.turn_completed and ran.command[0] == str(fake)


def test_an_npm_cli_brings_its_modules_and_a_script_its_interpreter(tmp_path):
    modules = tmp_path / "lib" / "node_modules"
    script = modules / "@scope" / "cli" / "dist" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text(f"#!/usr/bin/env -S {Path(sys.executable).name} -u\n")
    assert sandbox._install_root(script) == modules
    assert sandbox._install_root(Path("/usr/bin/codex")) == Path("/usr/bin/codex")
    assert sandbox._interpreter(script, str(Path(sys.executable).parent)) == [Path(os.path.realpath(sys.executable))]


def test_macos_hides_the_home_and_runtime_and_shows_the_workdir(tmp_path, monkeypatch):
    workdir, home = tmp_path / "work", tmp_path / "home"
    workdir.mkdir()
    monkeypatch.setattr(sandbox, "sandbox_tool", lambda: "/usr/bin/sandbox-exec")
    command = sandbox.isolated_workdir_command(
        ["/bin/echo"], backend="codex", working_dir=workdir, env={**os.environ, "HOME": str(home)},
    )
    assert command[:2] == ["/usr/bin/sandbox-exec", "-p"]
    hidden, shown = command[2].split("(require-not ", 1)
    assert f'(subpath "{home}")' in hidden and f'(subpath "{workdir}")' in shown
    assert command[-1] == os.path.realpath("/bin/echo")


def _codex_setup(tmp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
    """A codex home whose config names files the way a real one does."""
    home, codex, shared = tmp_path / "home", tmp_path / "codex", tmp_path / "shared"
    for path in (home, codex, shared, codex / "prompts"):
        path.mkdir(parents=True)
    files = {
        "catalog": shared / "models-1m.json",
        "instructions": codex / "prompts" / "base.md",
        "compact": home / "compact.md",
        "profile": codex / "fast.config.toml",
        "profile-catalog": shared / "fast-models.json",
        "sibling": shared / "other-secret.txt",
    }
    for path in files.values():
        path.write_text("{}\n")
    (codex / "config.toml").write_text(
        f'model_catalog_json = "{files["catalog"]}"\n'
        'model_instructions_file = "prompts/base.md"\n'
        'experimental_compact_prompt_file = "~/compact.md"\n'
        f'experimental_instructions_file = "{shared}"\n'
        f'[profiles.old]\nmodel_instructions_file = "{shared / "missing.md"}"\n'
    )
    files["profile"].write_text(f'model_catalog_json = "{files["profile-catalog"]}"\n')
    return {**os.environ, "HOME": str(home), "CODEX_HOME": str(codex)}, files


def _read_only_binds(command: list[str]) -> set[str]:
    return {command[i + 1] for i, arg in enumerate(command) if arg == "--ro-bind-try"}


def test_codex_brings_the_files_its_config_names_and_nothing_beside_them(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(sandbox, "sandbox_tool", lambda: "/usr/bin/bwrap")
    env, files = _codex_setup(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    command = sandbox.isolated_workdir_command(
        ["/bin/echo", "exec", "--profile", "fast"], backend="codex", working_dir=workdir, env=env,
    )
    bound = _read_only_binds(command)
    for label in ("catalog", "instructions", "compact", "profile", "profile-catalog"):
        assert str(files[label]) in bound, label
    assert str(files["catalog"].parent) not in bound  # a directory setting brings nothing
    assert str(files["sibling"]) not in bound
    assert str(files["catalog"].parent / "missing.md") not in bound

    # Another backend, or a config codex cannot parse, brings nothing extra.
    other = sandbox.isolated_workdir_command(["/bin/echo"], backend="claude", working_dir=workdir, env=env)
    assert str(files["catalog"]) not in _read_only_binds(other)
    (Path(env["CODEX_HOME"]) / "config.toml").write_text("model_catalog_json = [unclosed\n")
    broken = sandbox.isolated_workdir_command(["/bin/echo"], backend="codex", working_dir=workdir, env=env)
    assert str(files["catalog"]) not in _read_only_binds(broken)


def test_codex_reads_its_model_catalog_inside_the_sandbox(tmp_path, monkeypatch, host_sandbox):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "state"))
    env, files = _codex_setup(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = f"cat {files['catalog']} && echo > {files['catalog']}; cat {files['sibling']}"
    command = sandbox.isolated_workdir_command(
        ["/bin/sh", "-c", script], backend="codex", working_dir=workdir, env=env,
    )
    result = subprocess.run(command, env=env, cwd=workdir, capture_output=True, text=True, timeout=60)
    assert result.stdout == "{}\n", result.stderr
    assert "Read-only file system" in result.stderr
    assert "other-secret.txt" in result.stderr  # the catalog's neighbour stays out of sight
    assert files["catalog"].read_text() == "{}\n"
