"""Installed ``argus`` entrypoint: launch the bundled Ink cockpit."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _bundle_path() -> Path | None:
    explicit = os.environ.get("ARGUS_TUI_BUNDLE")
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        # Wheel layout (force-included by pyproject.toml).
        Path(__file__).resolve().parents[1] / "_frontend" / "tui" / "bundle" / "argus.mjs",
        # Source/editable checkout layout.
        Path(__file__).resolve().parents[2] / "frontend" / "tui" / "bundle" / "argus.mjs",
    ]
    return next((path for path in candidates if path is not None and path.is_file()), None)


def _node_major(node: str) -> int | None:
    try:
        completed = subprocess.run(
            [node, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+)", completed.stdout or completed.stderr or "")
    return int(match.group(1)) if match else None


def main(argv: list[str] | None = None) -> int:
    bundle = _bundle_path()
    if bundle is None:
        sys.stderr.write(
            "argus: bundled Ink TUI is missing. Reinstall from a current release.\n"
        )
        return 2
    node = shutil.which("node")
    if node is None:
        sys.stderr.write("argus: Ink TUI requires Node.js 18 or newer.\n")
        return 2
    major = _node_major(node)
    if major is None or major < 18:
        found = "unknown" if major is None else str(major)
        sys.stderr.write(
            f"argus: Ink TUI requires Node.js 18 or newer (found {found}).\n"
        )
        return 2
    forwarded = list(sys.argv[1:] if argv is None else argv)
    os.execv(node, [node, str(bundle), *forwarded])
    return 0  # pragma: no cover - execv replaces the process


__all__ = ["main"]
