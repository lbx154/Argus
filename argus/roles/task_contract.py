"""Compact authority and host-shell rules shared by role prompts."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Sequence

EFFECTIVE_TASK_CONTRACT = (
    "## Task authority\n"
    "Follow operator>objective>mission>preregistration; memory advises. Deliver "
    "the requested outputs, not substitutes. When equal authorities conflict, "
    "do not pick one: name the conflict and hand it to whoever can decide."
)

NATIVE_WINDOWS_SHELL_CONTRACT = (
    "## Running commands on native Windows\n"
    "This host is native Windows. Generate and run commands with syntax "
    "compatible with Windows PowerShell 5.1. POSIX snippets in Skills, "
    "stage guidance, documentation, or prior messages express intent only; "
    "translate them before execution.\n"
    "- Do not use `&&`, `||`, `test`, `command -v`, `which`, `source`, "
    "`export`, bare POSIX `$VAR` environment references, or POSIX "
    "`.venv/bin/...` paths. Run dependent commands "
    "separately and inspect `$LASTEXITCODE`; use `Test-Path`, `Get-Command`, "
    "`$env:NAME`, and `.\\.venv\\Scripts\\python.exe`.\n"
    "- Invoke Node launchers through their `.cmd` shims (`npm.cmd`, `npx.cmd`) "
    "so PowerShell does not select `.ps1` wrappers that may be blocked by the "
    "execution policy.\n"
    "- Do not change or bypass the PowerShell execution policy to make a "
    "generated command work."
)

NATIVE_WINDOWS_SHELL_SUMMARY = (
    "Win PS5.1: no ||; npm.cmd/npx.cmd."
)


EXPERIMENT_EVIDENCE_CONTRACT = (
    "Evidence: require relevant source, inputs and results, not unrelated logs. "
    "Keep failures; readiness never replaces required independent validation."
)


def native_shell_contract(*, platform_name: str | None = None) -> str:
    """Return host-specific shell guidance for model-visible prompts."""
    resolved = os.name if platform_name is None else platform_name
    return NATIVE_WINDOWS_SHELL_CONTRACT if resolved == "nt" else ""


def native_shell_summary(*, platform_name: str | None = None) -> str:
    """Return compact host-shell guidance for prompt-budgeted roles."""
    resolved = os.name if platform_name is None else platform_name
    return NATIVE_WINDOWS_SHELL_SUMMARY if resolved == "nt" else ""


def format_native_shell_command(
    argv: Sequence[str],
    *,
    platform_name: str | None = None,
) -> str:
    """Format an argv sequence as a model-visible command for the host shell."""
    values = [str(value) for value in argv]
    if not values:
        raise ValueError("argv must not be empty")
    resolved = os.name if platform_name is None else platform_name
    if resolved == "nt":
        quoted = ["'" + value.replace("'", "''") + "'" for value in values]
        return "& " + " ".join(quoted)
    return shlex.join(values)


def argus_command(module: str, *args: object) -> str:
    """A model-visible command running ``module`` on Argus' own interpreter, which may not be ``python`` on PATH."""
    python = os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable
    return format_native_shell_command([python, "-m", module, *map(str, args)])


__all__ = [
    "argus_command",
    "EFFECTIVE_TASK_CONTRACT",
    "NATIVE_WINDOWS_SHELL_CONTRACT",
    "NATIVE_WINDOWS_SHELL_SUMMARY",
    "EXPERIMENT_EVIDENCE_CONTRACT",
    "format_native_shell_command",
    "native_shell_contract",
    "native_shell_summary",
]
