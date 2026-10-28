"""``python -m argus``: the Python CLI.

``python -m argus`` is the pure command-line interface (it never starts the
Node cockpit; that is the ``argus`` console script, ``apps.tui_launcher``).
"""
from __future__ import annotations

import sys

from .apps.cli import main as _cli_main
from .apps.tui_launcher import _configure_windows_console_encoding


def main(argv: list[str] | None = None) -> int:
    """Run the Python CLI with a Windows-safe text console."""
    _configure_windows_console_encoding()
    return _cli_main(argv)

if __name__ == "__main__":
    sys.exit(main())
