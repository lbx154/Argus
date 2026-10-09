"""Snapshot the small VS Code launcher before handing it to a shell.

The extension rewrites this file when its host restarts. A shell can resume
reading the changed file after the long-running CLI child exits, then execute
the tail of its new third line as a command. Running an immutable snapshot
keeps the configured interpreter/CLI and environment without that race.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path


def is_vscode_copilot_launcher(path: str) -> bool:
    candidate = Path(path)
    return candidate.name == "copilot" and candidate.parent.name == "copilotCli"


def stable_copilot_command(command: list[str]) -> list[str]:
    if os.name == "nt" or not command or not is_vscode_copilot_launcher(command[0]):
        return command
    path = Path(command[0])
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        content = handle.read(16385)
        after = os.fstat(handle.fileno())
    if len(content) > 16384 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Copilot VS Code launcher changed while being read; retry the call")
    text = content.decode("utf-8")
    lines = text.splitlines()
    valid = len(lines) == 3 and lines[:2] == ["#!/bin/sh", "unset NODE_OPTIONS"]
    parts = shlex.split(lines[2]) if valid else []
    if not (len(parts) == 4 and parts[0] == "ELECTRON_RUN_AS_NODE=1"
            and Path(parts[1]).is_absolute() and Path(parts[2]).is_absolute()
            and Path(parts[2]).name == "copilotCLIShim.js" and parts[3] == "$@"):
        raise RuntimeError("Copilot VS Code launcher is incomplete or unsupported; configure the standalone Copilot CLI")
    return ["/bin/sh", "-c", text, command[0], *command[1:]]
