"""Agents see only what their call is given, by construction.

Two tenants share one OS account, as on a trial host, and each has two
projects. Every project's agent tries the escapes a path check cannot stop
(PR #302's review): a path assembled at run time, Python, a script it writes
and then runs, a symlink, a recursive search of the disk. Nothing of another
project or tenant, of the Argus runtime or of the portal key comes back; its
own work does.
"""
from __future__ import annotations

import json
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
        handed=[project.handoff], writable=writable, env=env,
    )
    return subprocess.run(command, env=env, cwd=project.workdir, capture_output=True, text=True, timeout=120)


def _escapes(target: Path, workdir: Path) -> list[str]:
    return [
        f"cat {target}",
        f"D={target.parents[1]}; cat $D/{target.parent.name}/{target.name}",
        f"python3 -c \"print(open('{target.parent}' + '/{target.name}').read())\"",
        f"printf 'cat {target}\\n' > escape.sh; sh escape.sh",
        f"(cd {target.parent} && cat {target.name})",
        f"cat {os.path.relpath(target, workdir)}",
        f"ln -sf {target} link; cat link",
    ]


def test_no_agent_reads_another_project_tenant_the_runtime_or_the_key(world, tmp_path, monkeypatch, host_sandbox):
    targets = [path for project in world.values() for path in project.files.values()]
    targets.append(tmp_path / "portal" / "key")
    for project in world.values():
        attempts = [line for target in targets for line in _escapes(target, project.workdir)]
        result = _run(project, "\n".join([*attempts, f"grep -rs PRIVATE {tmp_path}"]), monkeypatch)
        seen = result.stdout
        assert project.marker("workspace") in seen and project.marker("handoff") in seen, result.stderr
        leaked = [line for line in seen.splitlines() if line.startswith("PRIVATE") and line not in {
            project.marker("workspace"), project.marker("handoff"),
        }]
        # 4 projects x 6 files + the key, each tried 7 ways, and a disk search.
        assert len(attempts) == 25 * 7 and not leaked, leaked


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


def test_a_library_handed_from_inside_argus_stays_read_only(world, monkeypatch, host_sandbox):
    project = world["tenant-a", "alpha"]
    library = Path(sandbox.__file__).parents[1] / "builtin_skills"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.state))
    command = sandbox.isolated_workdir_command(
        ["/bin/sh", "-c", f"ls {library} >/dev/null && touch {library}/planted"],
        backend="codex", working_dir=project.workdir, handed=[library],
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert "Read-only file system" in result.stderr and not (library / "planted").exists()


def test_a_reading_call_cannot_write_even_its_workdir(world, monkeypatch, host_sandbox):
    project = world["tenant-a", "alpha"]
    result = _run(project, "cat results/secret.txt; echo x > results/new.txt", monkeypatch, writable=False)
    assert project.marker("workspace") in result.stdout
    assert not (project.workdir / "results" / "new.txt").exists()


def test_the_cli_keeps_its_login_but_not_other_projects_sessions(world, monkeypatch, host_sandbox):
    project = world["tenant-a", "alpha"]
    monkeypatch.delenv("CODEX_HOME")
    codex = project.home / ".codex"
    (codex / "auth.json").write_text('{"token": "old"}')
    (codex / "config.toml").write_text('model = "x"\n')
    result = _run(project, "\n".join([
        "cat $HOME/.codex/config.toml",
        "echo '{\"token\": \"refreshed\"}' > $HOME/.codex/auth.json",
        "echo hijack > $HOME/.codex/config.toml",
        "ls $HOME/.codex/sessions",
    ]), monkeypatch)
    assert 'model = "x"' in result.stdout, result.stderr
    assert json.loads((codex / "auth.json").read_text()) == {"token": "refreshed"}
    assert (codex / "config.toml").read_text() == 'model = "x"\n'
    assert "alpha.jsonl" not in result.stdout


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
