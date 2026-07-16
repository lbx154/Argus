"""Single frozen entrypoint used by the binary-only distribution."""

from __future__ import annotations

import os
import sys


def _configure_console_encoding() -> None:
    """Keep bilingual CLI output valid in PowerShell and captured CI pipes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """Dispatch the frozen executable to the cockpit or admin CLI."""
    _configure_console_encoding()
    os.environ.setdefault("ARGUS_BINARY_DISTRIBUTION", "1")
    # A frozen runtime has no writable source checkout to commit into.
    os.environ["ARGUS_SKILL_AUTOCOMMIT_SKILLS"] = "0"

    mode = os.environ.get("ARGUS_BINARY_MODE", "tui").strip().lower()
    if sys.argv[1:2] == ["--argus-internal-cli"]:
        mode = "cli"
        del sys.argv[1]

    if mode == "cli":
        from argus_skill.apps.cli._core import main as cli_main

        return cli_main(sys.argv[1:])

    from argus_skill.apps.tui_launcher import main as tui_main

    return tui_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
