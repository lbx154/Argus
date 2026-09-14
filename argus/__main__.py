"""``python -m argus_skill`` entry point and the [project.scripts] target.

The entry point declared in pyproject.toml is
``argus_skill.__main__:main`` — we re-export ``main`` from
``apps.cli`` here so that resolves correctly.
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
