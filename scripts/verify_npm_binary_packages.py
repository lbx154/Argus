#!/usr/bin/env python3
"""Reject npm package directories that accidentally contain source files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyo", ".ts", ".tsx", ".map"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("dist-binary/npm"))
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    cli = root / "cli"
    platforms = sorted(path for path in root.glob("cli-*-*") if path.is_dir())
    if not cli.is_dir() or not platforms:
        raise SystemExit(f"incomplete npm binary package tree: {root}")

    forbidden = sorted(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES
    )
    if forbidden:
        raise SystemExit(f"npm binary packages contain source-like files: {forbidden[:10]}")

    cli_package = json.loads((cli / "package.json").read_text(encoding="utf-8"))
    for platform in platforms:
        platform_package = json.loads(
            (platform / "package.json").read_text(encoding="utf-8")
        )
        binary = platform / platform_package["main"]
        if not binary.is_file():
            raise SystemExit(f"missing platform binary: {binary}")
        if os.name != "nt" and not os.access(binary, os.X_OK):
            raise SystemExit(f"platform binary is not executable: {binary}")
        expected = platform_package["version"]
        actual = cli_package.get("optionalDependencies", {}).get(
            platform_package["name"]
        )
        if actual != expected:
            raise SystemExit(
                f"launcher/platform version mismatch: expected {expected}, found {actual}"
            )
    print(f"npm binary package verification passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
