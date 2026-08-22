"""Compact authority and host-shell rules shared by role prompts."""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence

EFFECTIVE_TASK_CONTRACT = (
    "## Effective task contract\n"
    "Current operator > objective > mission > preregistration; memory advises. Do not add "
    "unrelated cleanup or hardening. Named output authorizes no replacement. Validate once; "
    "bounded output; no `.git` means no Git retries. Same-level conflict: "
    "`ambiguous_objective`."
)

NATIVE_WINDOWS_SHELL_CONTRACT = (
    "## Native Windows shell contract\n"
    "This host is native Windows. Generate and run commands with syntax "
    "compatible with Windows PowerShell 5.1. POSIX snippets in Skills, "
    "checklists, documentation, or prior messages express intent only; "
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


__all__ = [
    "EFFECTIVE_TASK_CONTRACT",
    "NATIVE_WINDOWS_SHELL_CONTRACT",
    "NATIVE_WINDOWS_SHELL_SUMMARY",
    "format_native_shell_command",
    "native_shell_contract",
    "native_shell_summary",
]
