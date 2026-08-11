from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.roles.prompts import engineer as engineer_prompts
from argus_skill.roles.prompts import planner as planner_prompts
from argus_skill.roles.task_contract import (
    format_native_shell_command,
    native_shell_contract,
    native_shell_summary,
)


def test_windows_contract_requires_powershell_51_and_cmd_shims() -> None:
    contract = native_shell_contract(platform_name="nt")

    assert "Windows PowerShell 5.1" in contract
    assert "`&&`" in contract and "`||`" in contract
    assert "npm.cmd" in contract and "npx.cmd" in contract
    assert "Do not change or bypass the PowerShell execution policy" in contract
    assert native_shell_contract(platform_name="posix") == ""
    assert native_shell_summary(platform_name="nt") == (
        "Native Windows: use PowerShell 5.1, no POSIX chains, and npm.cmd/npx.cmd."
    )
    assert native_shell_summary(platform_name="posix") == ""


def test_windows_command_formatter_uses_call_operator_and_powershell_quotes() -> None:
    command = format_native_shell_command(
        [r"C:\Program Files\Python\python.exe", "-c", "print('ok')"],
        platform_name="nt",
    )

    assert command == (
        "& 'C:\\Program Files\\Python\\python.exe' '-c' 'print(''ok'')'"
    )


def test_engineer_and_bounded_planner_receive_windows_contract(monkeypatch) -> None:
    contract = native_shell_contract(platform_name="nt")
    monkeypatch.setattr(engineer_prompts, "native_shell_contract", lambda: contract)
    monkeypatch.setattr(planner_prompts, "native_shell_contract", lambda: contract)

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
    planner = planner_prompts.build_bounded_dag_prompt("repair the command path")

    assert contract in engineer
    assert contract in continuation
    assert contract in planner


def test_mermaid_skill_has_an_execution_policy_safe_windows_command() -> None:
    package_root = Path(engineer_prompts.__file__).resolve().parents[2]
    text = (
        package_root
        / "builtin_skills"
        / "engineer"
        / "mermaid-graphviz-diagrams.md"
    ).read_text(encoding="utf-8")

    assert "Windows PowerShell" in text
    assert "npx.cmd --yes @mermaid-js/mermaid-cli" in text
    assert "npx.ps1" in text


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
