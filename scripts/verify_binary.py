#!/usr/bin/env python3
"""Smoke-test a frozen Argus executable before packaging it for npm."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(f"missing binary: {binary}")

    env = os.environ.copy()
    env["ARGUS_BINARY_MODE"] = "cli"
    result = subprocess.run(
        [str(binary), "--help"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    rendered = result.stdout + result.stderr
    if result.returncode != 0 or "Start Argus" not in rendered:
        raise SystemExit(
            f"binary CLI smoke failed ({result.returncode}):\n{rendered[-4000:]}"
        )

    leaked = sorted(binary.parent.glob("**/*.py"))
    if leaked:
        raise SystemExit(f"binary output contains Python source files: {leaked[:5]}")
    print(f"binary smoke passed: {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
