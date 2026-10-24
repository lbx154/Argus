from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.agent_cli import runner_backend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.runner_backend import (
    BACKEND_COPILOT,
    BACKEND_GROK,
    BACKEND_OPENCODE,
    BACKEND_PI,
    BACKEND_QODER,
    resolve_runner_bin,
)


def _write_runner_executable(path: Path, *, exit_code: int = 0) -> Path:
    if os.name == "nt":
        path = path.with_name(f"{path.name}.cmd")
        path.write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="utf-8")
        return path
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _assert_same_path(actual: str | None, expected: Path) -> None:
    assert actual is not None
    assert os.path.normcase(str(Path(actual).resolve())) == os.path.normcase(
        str(expected.resolve())
    )


@pytest.mark.parametrize(("backend", "binary", "install_dir"), [
    pytest.param(BACKEND_COPILOT, "copilot", ".local/bin", id="copilot-user-local"),
    pytest.param(BACKEND_OPENCODE, "opencode", ".opencode/bin", id="opencode-standard-install"),
    pytest.param(BACKEND_OPENCODE, "opencode", "", id="opencode-path"),
    pytest.param(BACKEND_PI, "pi", "", id="pi-path"),
    pytest.param(BACKEND_GROK, "grok", "", id="grok-path"),
    pytest.param(BACKEND_QODER, "qodercli", "", id="qoder-path"),
])
def test_backend_binary_resolution(tmp_path: Path, monkeypatch, backend, binary, install_dir) -> None:
    directory = tmp_path / install_dir
    directory.mkdir(parents=True, exist_ok=True)
    executable = _write_runner_executable(directory / binary)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "service-bin" if install_dir else tmp_path))
    _assert_same_path(resolve_runner_bin(backend), executable)
    _assert_same_path(AgentCliRunner(backend=backend).agent_bin, executable)


@pytest.mark.parametrize("suffix", [".CMD", ".EXE"])
def test_windows_runner_fallback_resolves_quoted_path_directory(
    tmp_path: Path, monkeypatch, suffix: str,
) -> None:
    bindir = tmp_path / "研发 tools%ARGUS_LOOKUP_LITERAL%"
    bindir.mkdir()
    executable = bindir / f"pi{suffix}"
    executable.write_text("local lookup fixture", encoding="utf-8")
    monkeypatch.setattr(runner_backend, "os", SimpleNamespace(
        name="nt", environ={
            "PATH": f'"{bindir}"',
            "PATHEXT": os.pathsep.join([".EXE", ".CMD"]),
            "ARGUS_LOOKUP_LITERAL": "must-not-expand",
        }, pathsep=os.pathsep,
    ))
    # Python's which() does not strip quotes from PATH entries. Exercise the
    # existing fallback on every host, including mixed-case Windows suffixes.
    monkeypatch.setattr(runner_backend.shutil, "which", lambda *_args: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    _assert_same_path(resolve_runner_bin(BACKEND_PI), executable)


def test_runner_skips_inaccessible_path_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    blocked = tmp_path / "blocked-bin"
    blocked.mkdir()
    blocked_candidate = blocked / "claude"
    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        if path == blocked_candidate:
            raise PermissionError("private launcher target")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("PATH", str(blocked))

    assert resolve_runner_bin("claude") is None


def test_windows_runner_fallback_rejects_directories_without_probing_unrelated_files(
    tmp_path: Path, monkeypatch,
) -> None:
    candidate = tmp_path / "pi"
    matching_directory = tmp_path / "pi.EXE"
    matching_directory.mkdir()
    unrelated = tmp_path / "unrelated.dll"
    unrelated.write_text("not a runner", encoding="utf-8")
    inspected: list[Path] = []
    original_is_file = Path.is_file

    def inspect(path: Path) -> bool:
        inspected.append(path)
        return original_is_file(path)

    monkeypatch.setattr(runner_backend, "os", SimpleNamespace(
        name="nt", environ={"PATHEXT": os.pathsep.join([".EXE", ".CMD"])},
        pathsep=os.pathsep,
    ))
    monkeypatch.setattr(runner_backend.shutil, "which", lambda *_args: None)
    monkeypatch.setattr(Path, "is_file", inspect)

    assert runner_backend._resolve_explicit_candidate(candidate) is None
    assert inspected == [candidate, matching_directory]
