#!/usr/bin/env python3
"""Atomically refresh Argus release identity and both production frontends."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NPM_COMMAND = "npm.cmd" if os.name == "nt" else "npm"


def run(*argv: str, cwd: Path = ROOT) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m argus.release_tools.build_release",
        description=(
            "Refresh the release identity and rebuild both production frontends "
            "in place: generated protocol types and fixtures, the release "
            "manifest, frontend/web/dist and frontend/tui/bundle. Run it from a "
            "source checkout with the project interpreter; there are no options."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    try:
        # Bundled verticals may ship independent workbench frontends. Build each
        # before the release manifest, keeping domain code out of the host UI.
        for manifest_path in sorted((ROOT / "argus" / "verticals").glob("*/workbench.json")):
            spec = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not spec.get("frontend"):
                continue
            frontend = (manifest_path.parent / spec["frontend"]).resolve()
            if manifest_path.parent.resolve() not in frontend.parents:
                raise ValueError("vertical frontend must remain inside its package")
            if not (frontend / "node_modules").is_dir():
                run(NPM_COMMAND, "ci", cwd=frontend)
            run(NPM_COMMAND, "run", "build", cwd=frontend)
        run(sys.executable, "-m", "argus.release_tools.build_plugins")
        # Generated protocol source is listed in the release manifest, so it
        # must be refreshed before computing the manifest. Reversing these two
        # steps makes a schema change require two builds: the first build updates
        # types and then correctly rejects its now-stale manifest.
        run(NPM_COMMAND, "run", "contracts:generate")
        run(
            sys.executable,
            "-m",
            "argus.release_tools.generate_manifest",
            "--prepare-build",
        )
        run(NPM_COMMAND, "run", "build", cwd=ROOT / "frontend" / "web")
        run(NPM_COMMAND, "run", "build", cwd=ROOT / "frontend" / "tui")
        run(
            sys.executable,
            "-m",
            "argus.release_tools.check_artifacts",
        )
    except subprocess.CalledProcessError as exc:
        return int(exc.returncode or 1)
    manifest = json.loads((ROOT / "argus" / "release_manifest.json").read_text())
    print(f"release ready: {manifest['package_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
