#!/usr/bin/env python3
"""Set one immutable binary release version inside an ephemeral CI checkout."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"could not update version in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not VERSION_RE.fullmatch(args[0]):
        raise SystemExit("usage: set_binary_release_version.py MAJOR.MINOR.PATCH")
    version = args[0]
    _replace_once(
        ROOT / "pyproject.toml",
        r'^version = "[^"]+"$',
        f'version = "{version}"',
    )
    _replace_once(
        ROOT / "argus_skill" / "__init__.py",
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{version}"',
    )
    subprocess.run(
        [sys.executable, "scripts/generate_release_manifest.py"],
        cwd=ROOT,
        check=True,
    )
    print(f"binary release version set to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
