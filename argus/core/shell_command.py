"""Model-visible shell commands for the host shell, run on Argus' own interpreter."""
from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Sequence


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


__all__ = ["argus_command", "format_native_shell_command"]
