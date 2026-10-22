"""SE-2: two tenants, two projects each; every role reaches for the others' files.

Each tenant has its own Argus runtime root with two projects (a workdir and a
state directory each). Both tenants live on one host as one OS user, the
case of an enrolled native trial workspace, so each runtime declares the
other tenant's directory in ``ARGUS_SKILL_FOREIGN_ROOTS``.

For each of the four Engineers and Reviewers, the boundary Argus computes for
it is handed to the real hook script exactly as each agent CLI calls it
(Claude Code, Codex and Copilot pre-tool hooks, and Pi's extension), with the
tool calls those CLIs actually send: file reads, writes, searches and shell
commands at the other three projects' workspaces, state and transcripts.
Every one must be refused; the role's own files stay readable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from argus.core.workspace_boundary import (
    FOREIGN_ROOTS_ENV,
    WorkspaceBoundary,
    hook_command,
    role_workspace_boundary,
)

TENANTS = ("tenant-a", "tenant-b")
PROJECTS = ("alpha", "beta")
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Project:
    tenant: str
    name: str
    home: Path  # the tenant's ARGUS_SKILL_HOME
    workdir: Path
    state: Path

    @property
    def files(self) -> dict[str, Path]:
        return {
            "workspace": self.workdir / "results" / "secret.txt",
            "state": self.state / "handoffs" / "task-1" / "mission.json",
            "transcript": self.home / "pi-sessions" / f"{self.name}.jsonl",
        }


@pytest.fixture
def world(tmp_path) -> dict[tuple[str, str], Project]:
    projects = {}
    for tenant in TENANTS:
        home = tmp_path / tenant / "home" / ".argus-skill"
        for name in PROJECTS:
            workdir = tmp_path / tenant / "work" / name
            state = home / "projects" / name
            project = Project(tenant, name, home, workdir, state)
            for label, path in project.files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{tenant}/{name}/{label}: private\n")
            (state / "session.json").write_text(json.dumps({"id": name, "workdir": str(workdir)}))
            projects[tenant, name] = project
    return projects


def _boundary(project: Project, monkeypatch) -> WorkspaceBoundary:
    other_tenant = next(t for t in TENANTS if t != project.tenant)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.home))
    monkeypatch.delenv("ARGUS_SKILL_PI_SESSION_DIR", raising=False)
    monkeypatch.setenv(FOREIGN_ROOTS_ENV, str(project.home.parents[2] / other_tenant))
    return role_workspace_boundary(
        working_dir=project.workdir,
        own_dirs=[project.state, project.state / "handoffs" / "task-1"],
    )


def _payloads(target: Path, cwd: Path) -> list[tuple[str, dict]]:
    """The calls each CLI's hook receives for one attempt on ``target``."""
    relative = Path(*([".."] * len(cwd.parts[1:]))) / target.relative_to(target.anchor)
    return [
        ("claude", {"tool_name": "Read", "tool_input": {"file_path": str(target)}}),
        ("claude", {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}}),
        ("claude", {"tool_name": "Grep", "tool_input": {"pattern": "private", "path": str(target.parent.parent)}}),
        ("claude", {"tool_name": "Bash", "tool_input": {"command": f"cat '{target}'"}}),
        ("copilot", {"tool_name": "Read", "tool_input": {"path": str(target)}}),
        ("copilot", {"tool_name": "Bash", "tool_input": {"command": f"head -n 5 {relative}"}}),
        ("codex", {"tool_name": "Bash", "tool_input": {"command": f"python3 -c \"print(open('{target}').read())\""}}),
        ("pi", {"tool_name": "read", "tool_input": {"path": str(target)}}),
        ("pi", {"tool_name": "edit", "tool_input": {"path": str(target), "edits": [{"oldText": "a", "newText": "b"}]}}),
        ("pi", {"tool_name": "bash", "tool_input": {"command": f"grep -rn private {target.parent}"}}),
    ]


def _hook(boundary_file: Path, fmt: str, payload: dict, cwd: Path) -> tuple[bool, str]:
    """Run the real hook script; ``True`` when it refuses the call."""
    result = subprocess.run(
        hook_command(boundary_file, fmt), input=json.dumps({**payload, "cwd": str(cwd)}),
        capture_output=True, text=True, timeout=30,
    )
    if fmt == "copilot":
        assert result.returncode == 0, result.stderr
        decision = json.loads(result.stdout or "{}").get("hookSpecificOutput", {})
        return decision.get("permissionDecision") == "deny", decision.get("permissionDecisionReason", "")
    assert result.returncode in (0, 2), result.stderr
    return result.returncode == 2, result.stderr


def test_every_role_is_refused_every_other_project_and_tenant(world, monkeypatch, tmp_path):
    attempts = refused = 0
    for (tenant, name), project in world.items():
        boundary_file = tmp_path / f"{tenant}-{name}.json"
        boundary_file.write_text(_boundary(project, monkeypatch).to_json())
        for other in world.values():
            if other == project:
                continue
            for label, target in other.files.items():
                for fmt, payload in _payloads(target, project.workdir):
                    attempts += 1
                    denied, reason = _hook(boundary_file, fmt, payload, project.workdir)
                    assert denied, (project, label, fmt, payload)
                    assert "workspace boundary" in reason
                    refused += 1
    # 4 roles x 3 other projects x 3 files x 10 calls
    assert attempts == refused == 360


def test_each_role_keeps_its_own_workspace_state_and_tools(world, monkeypatch, tmp_path):
    for project in world.values():
        boundary_file = tmp_path / "own.json"
        boundary_file.write_text(_boundary(project, monkeypatch).to_json())
        for label in ("workspace", "state"):
            for fmt, payload in _payloads(project.files[label], project.workdir):
                denied, reason = _hook(boundary_file, fmt, payload, project.workdir)
                assert not denied, (project, label, fmt, payload, reason)
        for command in ("ls -la && git status", "grep -rn TODO .", "pip install --target .pylibs numpy"):
            denied, reason = _hook(boundary_file, "codex", {
                "tool_name": "Bash", "tool_input": {"command": command}}, project.workdir)
            assert not denied, (command, reason)


def test_a_project_s_records_and_session_transcripts_are_closed_but_its_own_stay_open(world, monkeypatch):
    project = world["tenant-a", "alpha"]
    boundary = _boundary(project, monkeypatch)
    sibling = world["tenant-a", "beta"]
    cwd = str(project.workdir)

    assert boundary.refusal(str(sibling.state / "session.json"), cwd)
    assert boundary.refusal(str(project.home / "projects"), cwd, recursive=True)
    assert not boundary.refusal(str(project.state / "events.jsonl"), cwd)
    # A session store is closed, except the call's own session.
    store = Path(boundary.session_stores[0])
    assert boundary.refusal(str(store / "other-session.jsonl"), cwd)
    own_session = boundary.for_session("own-session")
    assert not own_session.refusal(str(store / "own-session" / "plan.md"), cwd)
    assert own_session.refusal(str(store / "other-session" / "plan.md"), cwd)


def test_a_link_inside_the_workspace_does_not_reach_another_project(world, monkeypatch):
    project, other = world["tenant-a", "alpha"], world["tenant-b", "beta"]
    link = project.workdir / "shared"
    link.symlink_to(other.workdir / "results", target_is_directory=True)
    boundary = _boundary(project, monkeypatch)

    for payload in (
        {"file_path": "shared/secret.txt"},
        {"command": "cat shared/secret.txt"},
        {"command": "cat ./sh*/secret.txt"},
    ):
        assert boundary.tool_refusal("Read" if "file_path" in payload else "Bash", payload, str(project.workdir))


def test_an_unreadable_or_altered_boundary_refuses_the_call(world, monkeypatch, tmp_path):
    project = world["tenant-a", "alpha"]
    boundary_file = tmp_path / "boundary.json"
    boundary_file.write_text(_boundary(project, monkeypatch).to_json())
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "notes.md"}, "cwd": str(project.workdir)})
    argv = hook_command(boundary_file, "claude")

    altered = subprocess.run([*argv, "--sha256", "0" * 64], input=payload, capture_output=True, text=True)
    missing = subprocess.run(hook_command(tmp_path / "gone.json", "claude"), input=payload,
                             capture_output=True, text=True)

    assert altered.returncode == 2 and "changed during the call" in altered.stderr
    assert missing.returncode == 2


def test_codex_exec_workdir_is_read_from_the_transcript(world, monkeypatch, tmp_path):
    """Codex's hook payload drops ``exec_command``'s ``workdir``; a relative
    command run from another project's directory is still refused."""
    project, other = world["tenant-a", "alpha"], world["tenant-a", "beta"]
    boundary = _boundary(project, monkeypatch)
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "response_item", "payload": {
        "type": "function_call", "name": "exec_command", "call_id": "call_7",
        "arguments": json.dumps({"cmd": "cat secret.txt", "workdir": str(other.workdir / "results")}),
    }}) + "\n")
    boundary_file = tmp_path / "b.json"
    boundary_file.write_text(boundary.to_json())

    denied, reason = _hook(boundary_file, "codex", {
        "tool_name": "Bash", "tool_input": {"command": "cat secret.txt"},
        "transcript_path": str(transcript), "tool_use_id": "call_7",
    }, project.workdir)

    assert denied and "workspace boundary" in reason


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to load the Pi extension")
def test_the_pi_extension_blocks_through_the_same_hook(world, monkeypatch, tmp_path):
    from argus.agent_cli._workspace_boundary import PI_EXTENSION, PI_HOOK_ENV

    project, other = world["tenant-a", "alpha"], world["tenant-b", "alpha"]
    boundary_file = tmp_path / "b.json"
    boundary_file.write_text(_boundary(project, monkeypatch).to_json())
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        "const ext = (await import(process.argv[2])).default;\n"
        "const handlers = {};\n"
        "await ext({on: (name, fn) => { handlers[name] = fn; }});\n"
        "const out = [];\n"
        "for (const path of JSON.parse(process.argv[3])) {\n"
        "  out.push(await handlers.tool_call({toolName: 'read', input: {path}}, {cwd: process.argv[4]}) || null);\n"
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    targets = [str(other.files["workspace"]), str(project.files["workspace"])]
    node = shutil.which("node")
    env = {"PATH": "/usr/bin:/bin", PI_HOOK_ENV: json.dumps(hook_command(boundary_file, "pi"))}
    result = subprocess.run(
        [node, str(driver), PI_EXTENSION.as_uri(), json.dumps(targets), str(project.workdir)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert result.returncode == 0, result.stderr
    blocked, allowed = json.loads(result.stdout)
    assert blocked["block"] is True and "workspace boundary" in blocked["reason"]
    assert allowed is None

    without_hook = subprocess.run(
        [node, str(driver), PI_EXTENSION.as_uri(), json.dumps(targets[1:]), str(project.workdir)],
        capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin"},
    )
    assert json.loads(without_hook.stdout)[0]["block"] is True


def test_the_hook_script_runs_without_the_package_on_its_path(tmp_path):
    """Agent CLIs run it with ``python -I``: no PYTHONPATH, no Argus import."""
    argv = hook_command(tmp_path / "b.json", "claude")
    assert argv[:2] == [sys.executable, "-I"]
    assert Path(argv[2]) == ROOT / "argus" / "core" / "workspace_boundary.py"


@pytest.mark.parametrize("command", [
    "cat {other}/results/secret.txt",
    "cat /{other}/results/secret.txt",
    "cat \"{parent}/\"'{name}'/results/secret.txt",
    "cat {parent}/{{{name},nothing}}/results/secret.txt",
    "cd {parent} && cat {name}/results/secret.txt",
    "cd {parent}; grep -rn private",
    "D={other}; cat $D/results/secret.txt",
    "python3 - <<'PY'\nprint(open('{other}/results/secret.txt').read())\nPY",
    "tar czf /tmp/x.tgz {parent}",
    "find {parent} -name secret.txt -exec cat {{}} +",
])
def test_common_shell_spellings_of_another_project_are_refused(world, monkeypatch, command):
    project, other = world["tenant-a", "alpha"], world["tenant-a", "beta"]
    boundary = _boundary(project, monkeypatch)
    text = command.format(other=other.workdir, parent=other.workdir.parent, name=other.workdir.name)
    assert boundary.command_refusal(text, str(project.workdir)), text
