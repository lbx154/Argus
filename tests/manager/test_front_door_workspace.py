from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.sandbox import forbidden_write_roots
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life import MemoryBundle
from argus_skill.manager import front_door


def test_operator_workspace_handles_missing_session_root(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert front_door._operator_workspace({}, None) == tmp_path


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
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "claude")

    def build(args):
        captured["args"] = args
        return sentinel

    monkeypatch.setattr("argus_skill.apps._runtime.build_life_runner", build)

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
    assert args.engineer_model == "gpt-5.5"
    assert args.reviewer_model == ""
    assert args.skills_dir == str(root / "skills")
    assert args.manager_memory is memory


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

    monkeypatch.setattr("argus_skill.apps._runtime.build_life_runner", build)

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

    monkeypatch.setattr("argus_skill.apps._runtime.build_life_runner", build)
    state = {"backend": "codex", "session_id": sid, "global_root": str(root)}

    front_door._ensure_manager_runner(state, memory)
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(memory.project.root), workdir=str(second)),
    )
    front_door._ensure_manager_runner(state, memory)

    assert built == [str(first.resolve()), str(second.resolve())]


def test_manager_runner_retries_after_transient_build_failure(
    tmp_path,
    monkeypatch,
) -> None:
    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-retry",
    )
    memory.init()
    calls = 0
    recovered = object()

    def build(args):  # noqa: ARG001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary runner startup failure")
        return recovered

    monkeypatch.setattr("argus_skill.apps._runtime.build_life_runner", build)
    state = {"backend": "codex"}

    assert front_door._ensure_manager_runner(state, memory) is None
    assert "manager_runner" not in state
    assert front_door._ensure_manager_runner(state, memory) is recovered
    assert calls == 2


def test_manager_runner_scopes_acp_to_session_id(tmp_path, monkeypatch) -> None:
    root = tmp_path / "root"
    sid = "s-private-acp"
    memory = MemoryBundle.for_cwd(tmp_path, global_root=root, fingerprint=sid)
    memory.init()
    default_scopes: list[str] = []
    manager_scopes: list[str] = []
    runner = SimpleNamespace(
        _backend=SimpleNamespace(set_acp_scope=default_scopes.append),
        manager_backend=SimpleNamespace(set_acp_scope=manager_scopes.append),
    )
    monkeypatch.setattr(
        "argus_skill.apps._runtime.build_life_runner",
        lambda args: runner,
    )

    result = front_door._ensure_manager_runner(
        {"backend": "copilot", "session_id": sid, "global_root": str(root)},
        memory,
    )

    assert result is runner
    assert default_scopes == [f"manager:{sid}"]
    assert manager_scopes == [f"manager:{sid}"]


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


def test_operator_workspace_still_prefers_an_explicit_session_root(
    tmp_path,
    monkeypatch,
) -> None:
    """The refusal only covers the guess. A caller-supplied root is untouched."""
    monkeypatch.chdir("/")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert front_door._operator_workspace({}, workspace) == workspace


def test_unresolvable_workspace_reports_instead_of_building_a_runner(
    tmp_path,
    monkeypatch,
) -> None:
    """Front-door triage goes unavailable — with a reason — rather than root at /.

    ``_ensure_manager_runner`` already logs and surfaces build failures to the
    operator; an unresolvable workspace joins that path instead of silently
    handing the runner a workspace it must not have.
    """
    # A memory whose project_root never resolved — the only way into the guess.
    memory = SimpleNamespace(
        project_root=None,
        root=tmp_path / "root",
        global_root=tmp_path / "root",
    )
    monkeypatch.chdir("/")

    def build(args):  # noqa: ARG001 — must never be reached
        raise AssertionError("runner was built against an unresolved workspace")

    monkeypatch.setattr("argus_skill.apps._runtime.build_life_runner", build)
    state = {"backend": "codex"}

    assert front_door._ensure_manager_runner(state, memory) is None
    assert "WorkspaceResolutionError" in state["manager_runner_error"]
    # Not cached: a later turn with a resolvable root must still get triage.
    assert "manager_runner" not in state
