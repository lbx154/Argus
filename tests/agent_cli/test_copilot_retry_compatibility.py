"""Issue #112: exercise a local version-selecting launcher, with no provider."""
from __future__ import annotations

import subprocess
import sys

import pytest

from argus_skill.agent_cli import copilot_acp
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


@pytest.mark.parametrize("acp_enabled", [False, True])
def test_cli_rollback_and_acp_startup_fallback_use_the_checked_loader(
    tmp_path, monkeypatch, acp_enabled,
):
    # Mirrors the issue's npm loader: --no-auto-update selects base 1.0.35,
    # whereas readiness/ACP normally select the installed update 1.0.83.
    launcher = tmp_path / "copilot_loader.py"
    launcher.write_text('''import json, sys
old = "--no-auto-update" in sys.argv
if "--version" in sys.argv:
    print("1.0.35" if old else "1.0.83")
elif old and "--context" in sys.argv:
    print("error: unknown option '--context'", file=sys.stderr)
    raise SystemExit(1)
else:
    sys.stdin.read()
    print(json.dumps({"type": "assistant.message", "data": {"content": "grounded"}}))
    print(json.dumps({"type": "result", "sessionId": "fake", "exitCode": 0}))
''', encoding="utf-8")
    checked_version = subprocess.run(
        [sys.executable, str(launcher), "--version"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert checked_version == "1.0.83"
    real_popen = subprocess.Popen
    commands = []

    def popen(command, *args, **kwargs):
        commands.append(command)
        if "--acp" in command:
            raise OSError("local ACP startup unavailable before any session")
        return real_popen([sys.executable, str(launcher), *command[1:]], *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1" if acp_enabled else "0")
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_ACP_LABELS", raising=False)
    runner = AgentCliRunner("copilot-bin", backend="copilot")
    try:
        for label in ("manager-classify-grounded", "manager-classify-grounded-retry"):
            result = runner.run_exec(
                prompt="inspect repository",
                resume_thread_id=None,
                options=RunnerOptions(
                    working_dir=str(tmp_path),
                    sandbox_mode="read-only",
                    extra_args=["--context", "default"],
                ),
                run_label=label,
            )
            assert result.turn_completed, (result.fatal_error, result.stderr_lines)
            assert result.agent_messages == ["grounded"]
        assert all("--no-auto-update" not in command for command in commands)
        assert sum("--acp" in command for command in commands) == (2 if acp_enabled else 0)
    finally:
        copilot_acp.close_clients_for_scope(runner._acp_scope)
