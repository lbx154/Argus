#!/usr/bin/env python3
"""Generate the legacy Python runtime's release manifest from shipped source."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.release import MANIFEST_SCHEMA_VERSION, source_files  # noqa: E402

MANIFEST_PATH = ROOT / "argus" / "release_manifest.json"


def _head_commit() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _recorded_commit() -> str:
    try:
        return str(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("git_commit") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def render(commit: str) -> str:
    """The manifest for a Python runtime build started at ``commit``."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = os.environ.get("ARGUS_RELEASE_VERSION", "").strip() or str(
        project["project"]["version"]
    )
    release_id = f"{package_version}+{commit[:12]}" if commit else package_version
    manifest = json.dumps(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_version": package_version,
            "git_commit": commit,
            "release_id": release_id,
            "source_files": source_files(ROOT),
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument(
        "--prepare-build",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if not args.check and not args.prepare_build:
        parser.error(
            "release identity cannot be updated without rebuilding the shipped "
            "frontends; run python -m argus.release_tools.build_release"
        )
    # A build commit sits on top of the commit it was built from, so a check
    # keeps the recorded commit and asks only whether the source still matches.
    manifest = render(_recorded_commit() if args.check else _head_commit())
    if args.check:
        current_manifest = (
            MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else ""
        )
        if current_manifest != manifest:
            raise SystemExit(
                "release manifest is stale; run python -m argus.release_tools.build_release"
            )
        return 0
    MANIFEST_PATH.write_text(manifest, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
