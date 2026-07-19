from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.apps.cli import _core
from argus_skill.core import paths as core_paths
from argus_skill.core.session import SessionMeta, read_session_meta, write_session_meta


def _args() -> SimpleNamespace:
    return SimpleNamespace(
        backend="memory",
        continuous=False,
        objective="",
        resume_continuous=False,
        bounded=False,
    )


def test_cli_resume_uses_persisted_workdir_not_shell_cwd(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "state"
    sid = "s-cli-workdir"
    state_dir = root / "projects" / sid
    workspace = tmp_path / "workspace"
    other_cwd = tmp_path / "other"
    state_dir.mkdir(parents=True)
    workspace.mkdir()
    other_cwd.mkdir()
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(state_dir), workdir=str(workspace)),
    )
    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (sid, False),
    )

    bundle = _core._resolve_project_bundle(_args())
    config = _core._build_worker_config(_args())

    assert bundle.project_worktree == workspace.resolve()
    assert config.project_workdir == workspace.resolve()
    assert config.life_dir == state_dir


def test_cli_legacy_resume_persists_first_explicit_workdir(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "state"
    sid = "legacy-session"
    (root / "projects" / sid).mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (sid, False),
    )

    bundle = _core._resolve_project_bundle(_args())
    meta = read_session_meta(root, sid)

    assert bundle.project_worktree == workspace.resolve()
    assert meta is not None
    assert meta.workdir == str(workspace.resolve())


def test_cli_legacy_resume_prefers_last_daemon_workdir_over_state_cwd(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "state"
    sid = "legacy-session"
    state_dir = root / "projects" / sid
    workspace = tmp_path / "workspace"
    state_dir.mkdir(parents=True)
    workspace.mkdir()
    monkeypatch.chdir(state_dir)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (sid, False),
    )
    monkeypatch.setattr(
        "argus_skill.daemon.state.read_daemon_status",
        lambda _path: SimpleNamespace(project_workdir=str(workspace)),
    )

    bundle = _core._resolve_project_bundle(_args())
    meta = read_session_meta(root, sid)

    assert bundle.project_worktree == workspace.resolve()
    assert meta is not None
    assert meta.workdir == str(workspace.resolve())


@pytest.mark.parametrize("relative_cwd", [".", "artifacts"])
def test_cli_legacy_resume_refuses_state_tree_as_workdir(
    tmp_path,
    monkeypatch,
    relative_cwd,
) -> None:
    root = tmp_path / "state"
    sid = "legacy-session"
    state_dir = root / "projects" / sid
    state_dir.mkdir(parents=True)
    current = state_dir / relative_cwd
    current.mkdir(exist_ok=True)
    monkeypatch.chdir(current)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (sid, False),
    )

    with pytest.raises(
        core_paths.PathResolutionError,
        match="no trustworthy workdir",
    ):
        _core._resolve_project_bundle(_args())

    assert read_session_meta(root, sid) is None


def test_cli_legacy_resume_does_not_recreate_deleted_session(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "state"
    sid = "deleted-session"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (sid, False),
    )

    with pytest.raises(
        core_paths.PathResolutionError,
        match="state directory is unavailable",
    ):
        _core._resolve_project_bundle(_args())

    assert read_session_meta(root, sid) is None


def test_cli_daemon_uses_persisted_shared_backend(tmp_path, monkeypatch) -> None:
    root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(_core, "_resolve_global_root", lambda _args: root)
    monkeypatch.setattr(
        _core,
        "_resolve_session_id",
        lambda *_args, **_kwargs: (None, False),
    )
    from argus_skill.core.knob_store import write_persisted_knob

    assert write_persisted_knob("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    args = _args()
    args.backend = None

    config = _core._build_worker_config(args)

    assert config.backend == "copilot"
