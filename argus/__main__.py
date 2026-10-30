"""``python -m argus``: the Python CLI, and the ``argus-skill`` script target.

The Python ``argus`` command serves the web cockpit by default and keeps its
explicit administration commands for existing projects.
The pre-rename console script ``argus-skill`` points here too for one release
and announces itself on stderr once per run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .apps.cli import main as _cli_main

LEGACY_COMMAND = "argus-skill"
_LEGACY_LAUNCHER_NAMES = frozenset({LEGACY_COMMAND, f"{LEGACY_COMMAND}.exe", f"{LEGACY_COMMAND}-script.py"})
LEGACY_COMMAND_NOTICE = (
    "argus-skill: this command is now `argus`; the `argus-skill` name is kept for one release.\n"
)


def _invoked_as_legacy_command(argv0: str | None = None) -> bool:
    name = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "")
    return Path(str(name or "")).name.lower() in _LEGACY_LAUNCHER_NAMES


def main(argv: list[str] | None = None) -> int:
    """Run the Python CLI with a Windows-safe text console."""
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
    if _invoked_as_legacy_command():
        sys.stderr.write(LEGACY_COMMAND_NOTICE)
        sys.stderr.flush()
    return _cli_main(argv)

if __name__ == "__main__":
    sys.exit(main())
