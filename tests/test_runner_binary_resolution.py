from __future__ import annotations

from pathlib import Path

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.runner_backend import (
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
    resolve_available_runner,
    resolve_runner_bin,
)


def test_runner_resolves_user_local_bin_when_service_path_omits_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".local" / "bin" / "copilot"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert resolve_runner_bin(BACKEND_COPILOT) == str(executable)
    assert AgentCliRunner(backend=BACKEND_COPILOT).agent_bin == str(executable)


def test_opencode_runner_uses_opencode_binary(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_runner_bin(BACKEND_OPENCODE) == str(executable)
    assert AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin == str(executable)


def test_pi_runner_uses_pi_binary(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_runner_bin(BACKEND_PI) == str(executable)
    assert AgentCliRunner(backend=BACKEND_PI).agent_bin == str(executable)


def test_opencode_runner_resolves_standard_install_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".opencode" / "bin" / "opencode"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert resolve_runner_bin(BACKEND_OPENCODE) == str(executable)
    assert AgentCliRunner(backend=BACKEND_OPENCODE).agent_bin == str(executable)


def test_missing_codex_falls_back_to_available_copilot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    copilot = tmp_path / "bin" / "copilot"
    copilot.parent.mkdir()
    copilot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    copilot.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(copilot.parent))

    assert resolve_available_runner(BACKEND_CODEX) == (
        BACKEND_COPILOT,
        str(copilot),
    )


def test_existing_codex_never_falls_back_on_runtime_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    codex = bindir / "codex"
    codex.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    codex.chmod(0o755)
    copilot = bindir / "copilot"
    copilot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    copilot.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(bindir))

    assert resolve_available_runner(BACKEND_CODEX) == (
        BACKEND_CODEX,
        str(codex),
    )


def test_unknown_backend_typo_does_not_fall_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    copilot = tmp_path / "copilot"
    copilot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    copilot.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_available_runner("codexx") == (BACKEND_CODEX, "codex")
