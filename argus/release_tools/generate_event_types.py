#!/usr/bin/env python3
"""Compatibility entry point for the TypeScript-owned contract generator."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from argus.core.contract_resources import contract_schema_path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = contract_schema_path("event_payload_schemas.json")
OUTPUT_PATH = ROOT / "packages/contracts/src/eventPayloads.generated.ts"
_GENERATOR = ROOT / "packages/contracts/scripts/generate.ts"


def _run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--experimental-strip-types", str(_GENERATOR), *args],
        cwd=ROOT, check=True, text=True, encoding="utf-8", capture_output=capture,
    )


def render() -> str:
    return _run("--render", "event-types", capture=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return _run(*(["--check"] if args.check else [])).returncode


if __name__ == "__main__":
    raise SystemExit(main())
