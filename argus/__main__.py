"""``python -m argus``: the Python CLI, and the ``argus-skill`` script target.

``python -m argus`` is the pure command-line interface (it never starts the
Node cockpit; that is the ``argus`` console script, ``apps.tui_launcher``).
The pre-rename console script ``argus-skill`` points here too for one release
and announces itself on stderr once per run.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .apps.cli import main as _cli_main
from .apps.tui_launcher import _configure_windows_console_encoding

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
    _configure_windows_console_encoding()
    if _invoked_as_legacy_command():
        sys.stderr.write(LEGACY_COMMAND_NOTICE)
        sys.stderr.flush()
    return _cli_main(argv)

if __name__ == "__main__":
    sys.exit(main())
