from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.sandbox import forbidden_write_roots
from argus.core.session import SessionMeta, write_session_meta
from argus.life import MemoryBundle
from argus.manager import front_door


def test_manager_runner_uses_persisted_workdir_without_moving_state_root(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "root"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sid = "s-workspace1"
    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=root,
        fingerprint=sid,
    )
    memory.init()
    write_session_meta(
        root,
        SessionMeta(
            id=sid,
            cwd=str(memory.project.root),
            workdir=str(workspace),
            launch_cwd=str(workspace),
        ),
    )
    captured = {}
    sentinel = object()
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "claude")  # retired, ignored

    def build(args):
        captured["args"] = args
        return sentinel

    monkeypatch.setattr("argus.apps._runtime.build_life_runner", build)

    result = front_door._ensure_manager_runner(
        {
            "backend": "codex",
            "session_id": sid,
            "global_root": str(root),
        },
        memory,
    )

    args = captured["args"]
    assert result is sentinel
    assert args.workdir == str(workspace.resolve())
    assert args.manager_session_root == str(memory.project.root)
    assert args.project_state_dir == str(memory.project.root)
    assert args.global_root == str(root)
    assert args.skills_dir == str(root / "skills")
    assert args.operator_workspace == str(workspace.resolve())
    assert args.engineer_model == "gpt-6.1-sol"
    assert args.reviewer_model == "gpt-6.1-sol"
    assert args.skills_dir == str(root / "skills")
    assert args.manager_memory is memory
    # The Manager's own recall reads the project's Wiki and Skills from its
    # workspace, not from the directory the server happened to start in.
    assert memory.project_worktree == workspace.resolve()


def test_manager_runner_falls_back_when_launch_cwd_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "root"
    sid = "s-workspace2"
    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=root,
        fingerprint=sid,
    )
    memory.init()
    write_session_meta(
        root,
        SessionMeta(
            id=sid,
            cwd=str(memory.project.root),
            launch_cwd=str(tmp_path / "missing"),
        ),
    )
    captured = {}

    def build(args):
        captured["args"] = args
        return object()

    monkeypatch.setattr("argus.apps._runtime.build_life_runner", build)

    front_door._ensure_manager_runner(
        {
            "backend": "codex",
            "session_id": sid,
            "global_root": str(root),
        },
        memory,
    )

    assert captured["args"].workdir == str(memory.project.root.resolve())
    assert captured["args"].operator_workspace == str(memory.project.root.resolve())


def test_manager_runner_rebuilds_when_persisted_workdir_changes(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "root"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    sid = "s-workspace3"
    memory = MemoryBundle.for_cwd(tmp_path, global_root=root, fingerprint=sid)
    memory.init()
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(memory.project.root), workdir=str(first)),
    )
    built: list[str] = []

    def build(args):
        built.append(args.workdir)
        return object()

    monkeypatch.setattr("argus.apps._runtime.build_life_runner", build)
    state = {"backend": "codex", "session_id": sid, "global_root": str(root)}

    front_door._ensure_manager_runner(state, memory)
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(memory.project.root), workdir=str(second)),
    )
    front_door._ensure_manager_runner(state, memory)

    assert built == [str(first.resolve()), str(second.resolve())]


def test_operator_workspace_refuses_the_filesystem_root(monkeypatch) -> None:
    """A detached daemon chdirs to ``/``; that is not a project workspace.

    ``spawn_detached_daemon`` runs ``os.chdir("/")`` before the worker starts,
    so an unresolved session root used to hand the Manager runner a writable
    workspace rooted at the whole filesystem — the hazard
    ``core.sandbox.fail_closed_workdir`` already guards for spawned roles.
    """
    monkeypatch.chdir("/")
    with pytest.raises(front_door.WorkspaceResolutionError) as excinfo:
        front_door._operator_workspace({}, None)
    assert "detached daemon" in str(excinfo.value)


def test_operator_workspace_refuses_a_forbidden_root(monkeypatch, tmp_path) -> None:
    """The gate brain is never a workspace, however we arrived in it."""
    gate = Path(forbidden_write_roots()[0])
    gate.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(gate)
    with pytest.raises(front_door.WorkspaceResolutionError) as excinfo:
        front_door._operator_workspace({}, None)
    assert str(gate) in str(excinfo.value)
