from __future__ import annotations

import os
from pathlib import Path

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


def _same_path(actual: str | None, expected: Path) -> bool:
    return bool(actual) and os.path.normcase(str(Path(actual).resolve())) == os.path.normcase(
        str(expected.resolve())
    )


def _fake_executable(path: Path, *, exit_code: int = 0) -> Path:
    if os.name == "nt":
        path = path.with_suffix(".cmd")
        path.write_text(f"@exit /b {exit_code}\n", encoding="ascii")
    else:
        path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
        path.chmod(0o755)
    return path


def test_runner_resolves_user_local_bin_when_service_path_omits_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".local" / "bin" / "copilot"
    executable.parent.mkdir(parents=True)
    executable = _fake_executable(executable)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "service-bin"))

    assert _same_path(resolve_runner_bin(BACKEND_COPILOT), executable)
    assert _same_path(AgentCliRunner(backend=BACKEND_COPILOT).agent_bin, executable)


def test_opencode_runner_uses_opencode_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _fake_executable(tmp_path / "opencode")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _same_path(resolve_runner_bin(BACKEND_OPENCODE), executable)
    assert _same_path(AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin, executable)


def test_pi_runner_uses_pi_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _fake_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _same_path(resolve_runner_bin(BACKEND_PI), executable)
    assert _same_path(AgentCliRunner(backend=BACKEND_PI).agent_bin, executable)


def test_grok_runner_uses_grok_binary(tmp_path: Path, monkeypatch) -> None:
    executable = _fake_executable(tmp_path / "grok")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _same_path(resolve_runner_bin(BACKEND_GROK), executable)
    assert _same_path(AgentCliRunner(backend=BACKEND_GROK).agent_bin, executable)


def test_qoder_runner_uses_qodercli_binary(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "qodercli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_runner_bin(BACKEND_QODER) == str(executable)
    assert AgentCliRunner(backend=BACKEND_QODER).agent_bin == str(executable)


def test_opencode_runner_resolves_standard_install_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".opencode" / "bin" / "opencode"
    executable.parent.mkdir(parents=True)
    executable = _fake_executable(executable)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "service-bin"))

    assert _same_path(resolve_runner_bin(BACKEND_OPENCODE), executable)
    assert _same_path(AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin, executable)


def test_missing_codex_falls_back_to_available_copilot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    copilot = tmp_path / "bin" / "copilot"
    copilot.parent.mkdir()
    copilot = _fake_executable(copilot)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(copilot.parent))

    backend, resolved = resolve_available_runner(BACKEND_CODEX)
    assert backend == BACKEND_COPILOT
    assert _same_path(resolved, copilot)


def test_existing_codex_never_falls_back_on_runtime_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    codex = _fake_executable(bindir / "codex", exit_code=1)
    _fake_executable(bindir / "copilot")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(bindir))

    backend, resolved = resolve_available_runner(BACKEND_CODEX)
    assert backend == BACKEND_CODEX
    assert _same_path(resolved, codex)


def test_unknown_backend_typo_does_not_fall_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _fake_executable(tmp_path / "copilot")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_available_runner("codexx") == (BACKEND_CODEX, "codex")
