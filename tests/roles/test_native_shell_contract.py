from __future__ import annotations

import os
import subprocess
import sys

import pytest

from argus.roles.prompts import engineer as engineer_prompts
from argus.roles.task_contract import (
    format_native_shell_command,
    native_shell_contract,
    native_shell_summary,
)


def test_windows_command_formatter_uses_call_operator_and_powershell_quotes() -> None:
    command = format_native_shell_command(
        [r"C:\Program Files\Python\python.exe", "-c", "print('ok')"],
        platform_name="nt",
    )

    assert command == (
        "& 'C:\\Program Files\\Python\\python.exe' '-c' 'print(''ok'')'"
    )


def test_engineer_receives_windows_contract(monkeypatch) -> None:
    contract = native_shell_contract(platform_name="nt")
    summary = native_shell_summary(platform_name="nt")
    monkeypatch.setattr(engineer_prompts, "native_shell_contract", lambda: contract)
    monkeypatch.setattr(engineer_prompts, "native_shell_summary", lambda: summary)

    engineer = engineer_prompts.build_mission_prompt(
        task="repair the Windows command path",
        skill_text="",
        next_action=None,
    )
    continuation = engineer_prompts.build_mission_prompt(
        task="repair the Windows command path",
        skill_text="",
        next_action=None,
        include_static=False,
    )

    assert summary in engineer
    assert contract not in engineer
    assert contract in continuation


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell integration test")
def test_formatted_windows_command_runs_under_restricted_policy() -> None:
    command = format_native_shell_command(
        [sys.executable, "-c", "print('argus-windows-ok')"],
        platform_name="nt",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Restricted",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "argus-windows-ok"


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell 5.1 regression")
def test_powershell_51_rejects_posix_operator_and_accepts_documented_branch() -> None:
    def run(command):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="mbcs", check=False, timeout=10,
        )

    old = run("Write-Output first || Write-Output second")
    assert old.returncode != 0
    assert "InvalidEndOfLine" in old.stderr
    python_exit = format_native_shell_command([sys.executable, "-c", "raise SystemExit(7)"])
    corrected = run(python_exit + "; if ($LASTEXITCODE -ne 0) { Write-Output recovered; exit 0 }")
    assert corrected.returncode == 0, corrected.stderr
    assert corrected.stdout.strip() == "recovered"


@pytest.mark.skipif(os.name != "nt", reason="native Restricted-policy launcher regression")
def test_npx_cmd_avoids_blocked_powershell_wrapper_without_changing_policy(tmp_path) -> None:
    # Synthetic shims exercise PowerShell resolution without downloading npm
    # packages or changing any user/machine execution-policy setting.
    (tmp_path / "npx.ps1").write_text("Write-Output 'wrong-wrapper'", encoding="utf-8")
    (tmp_path / "npx.cmd").write_text("@echo off\r\necho cmd-wrapper-ok\r\n", encoding="ascii")
    environment = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}

    def run(command):
        return subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Restricted", "-Command", command,
            ],
            env=environment, capture_output=True, text=True, encoding="mbcs", check=False, timeout=10,
        )

    resolved = run("(Get-Command npx).Source")
    assert resolved.stdout.strip().endswith("npx.ps1")
    blocked = run("npx")
    assert blocked.returncode != 0
    assert "PSSecurityException" in blocked.stderr
    allowed = run(format_native_shell_command(["npx.cmd"]))
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout.strip() == "cmd-wrapper-ok"
