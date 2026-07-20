from __future__ import annotations

from pathlib import Path

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.runner_backend import (
    BACKEND_COPILOT,
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
