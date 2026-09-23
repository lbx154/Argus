#!/usr/bin/env python3
"""Compatibility entry point for the TypeScript-owned contract generator."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = ROOT / "packages/contracts/scripts/generate.ts"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return subprocess.run(
        ["node", "--experimental-strip-types", str(_GENERATOR), *(["--check"] if args.check else [])],
        cwd=ROOT, check=True,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
