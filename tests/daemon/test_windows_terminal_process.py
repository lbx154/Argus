from pathlib import Path
from types import SimpleNamespace

from argus_skill.daemon import process


def _config(tmp_path: Path):
    root = tmp_path / "runtime"
    return SimpleNamespace(
        global_root=root,
        life_dir=root / "projects" / "session-1",
        project_workdir=tmp_path / "project",
        backend="codex",
        continuous=False,
        continuous_objective="",
        resume_continuous=False,
        continuous_open_ended=True,
    )


def test_frozen_windows_worker_reenters_same_binary(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(process.sys, "executable", r"C:\Argus\argus-core.exe")
    monkeypatch.setattr(process.sys, "frozen", True, raising=False)

    assert process._windows_daemon_command(config) == [
        r"C:\Argus\argus-core.exe",
        "--daemon-fg",
        "--life-dir",
        str(config.global_root),
        "--resume",
        "session-1",
        "--backend",
        "codex",
    ]


def test_source_windows_worker_uses_python_module(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(process.sys, "executable", "python.exe")
    monkeypatch.delattr(process.sys, "frozen", raising=False)

    command = process._windows_daemon_command(config)
    assert command[:3] == ["python.exe", "-m", "argus_skill"]
    assert command[-4:] == ["--resume", "session-1", "--backend", "codex"]
