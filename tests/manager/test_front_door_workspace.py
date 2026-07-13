from __future__ import annotations

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life import MemoryBundle
from argus_skill.manager import front_door


def test_operator_workspace_handles_missing_session_root(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert front_door._operator_workspace({}, None) == tmp_path


def test_manager_runner_exposes_launch_cwd_without_moving_state_root(
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
            launch_cwd=str(workspace),
        ),
    )
    captured = {}
    sentinel = object()

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
    assert args.workdir == str(memory.project.root)
    assert args.manager_session_root == str(memory.project.root)
    assert args.project_state_dir == str(memory.project.root)
    assert args.operator_workspace == str(workspace.resolve())


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

    assert captured["args"].operator_workspace == str(memory.project.root)
