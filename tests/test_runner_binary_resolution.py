from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.runner_backend import (
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_GROK,
    BACKEND_OPENCODE,
    BACKEND_PI,
    BACKEND_QODER,
    resolve_available_runner,
    resolve_runner_bin,
)
from argus_skill.core.knobs import resolve_runner_bin_setting


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


def test_persisted_runner_bin_stays_bound_to_its_backend() -> None:
    persisted = {
        "ARGUS_SKILL_RUNNER_BACKEND": "dsh",
        "ARGUS_SKILL_RUNNER_BIN": "/opt/bin/dsh",
    }

    assert (
        resolve_runner_bin_setting(
            backend="copilot",
            env={},
            persisted=persisted,
        )
        == ""
    )
    assert (
        resolve_runner_bin_setting(
            backend="dsh",
            env={},
            persisted=persisted,
        )
        == "/opt/bin/dsh"
    )
    assert (
        resolve_runner_bin_setting(
            env={"ARGUS_SKILL_RUNNER_BACKEND": "copilot"},
            persisted=persisted,
        )
        == ""
    )
    assert (
        resolve_runner_bin_setting(
            backend="opencode",
            env={},
            persisted={
                "ARGUS_SKILL_RUNNER_BACKEND": "opencod",
                "ARGUS_SKILL_RUNNER_BIN": "/opt/bin/opencode",
            },
        )
        == "/opt/bin/opencode"
    )


def test_runner_resolves_user_local_bin_when_service_path_omits_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".local" / "bin" / "copilot"
    executable.parent.mkdir(parents=True)
    executable = _write_runner_executable(executable)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "service-bin"))

    _assert_same_path(resolve_runner_bin(BACKEND_COPILOT), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_COPILOT).agent_bin, executable)


def test_opencode_runner_uses_opencode_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _write_runner_executable(tmp_path / "opencode")
    monkeypatch.setenv("PATH", str(tmp_path))

    _assert_same_path(resolve_runner_bin(BACKEND_OPENCODE), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin, executable)


def test_pi_runner_uses_pi_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _write_runner_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", str(tmp_path))

    _assert_same_path(resolve_runner_bin(BACKEND_PI), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_PI).agent_bin, executable)


def test_grok_runner_uses_grok_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _write_runner_executable(tmp_path / "grok")
    monkeypatch.setenv("PATH", str(tmp_path))

    _assert_same_path(resolve_runner_bin(BACKEND_GROK), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_GROK).agent_bin, executable)


def test_qoder_runner_uses_qodercli_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _write_runner_executable(tmp_path / "qodercli")
    monkeypatch.setenv("PATH", str(tmp_path))

    _assert_same_path(resolve_runner_bin(BACKEND_QODER), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_QODER).agent_bin, executable)


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


def test_opencode_runner_resolves_standard_install_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".opencode" / "bin" / "opencode"
    executable.parent.mkdir(parents=True)
    executable = _write_runner_executable(executable)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "service-bin"))

    _assert_same_path(resolve_runner_bin(BACKEND_OPENCODE), executable)
    _assert_same_path(AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin, executable)


def test_missing_codex_falls_back_to_available_copilot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    copilot = tmp_path / "bin" / "copilot"
    copilot.parent.mkdir()
    copilot = _write_runner_executable(copilot)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(copilot.parent))

    backend, runner_bin = resolve_available_runner(BACKEND_CODEX)
    assert backend == BACKEND_COPILOT
    _assert_same_path(runner_bin, copilot)


def test_existing_codex_never_falls_back_on_runtime_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    codex = _write_runner_executable(bindir / "codex", exit_code=1)
    _write_runner_executable(bindir / "copilot")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(bindir))

    backend, runner_bin = resolve_available_runner(BACKEND_CODEX)
    assert backend == BACKEND_CODEX
    _assert_same_path(runner_bin, codex)


def test_unknown_backend_typo_is_rejected_not_coerced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A typo'd backend must fail loudly, not become codex.

    This test used to assert ``resolve_available_runner("codexx") ==
    (BACKEND_CODEX, "codex")`` — i.e. it pinned the silent coercion in place.
    Under that behaviour an operator who mistyped ``ARGUS_SKILL_*_BACKEND``
    got a run that looked healthy while executing on a provider they never
    chose, and nothing anywhere named the value they had actually typed.
    """
    _write_runner_executable(tmp_path / "copilot")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ValueError) as excinfo:
        resolve_available_runner("codexx")
    message = str(excinfo.value)
    assert "codexx" in message
    # and it must tell the operator what they could have typed instead
    assert "copilot" in message
