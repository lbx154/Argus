#!/usr/bin/env python3
"""Atomically refresh Argus release identity and both production frontends."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NPM_COMMAND = "npm.cmd" if os.name == "nt" else "npm"


def run(*argv: str, cwd: Path = ROOT) -> None:
    env = os.environ.copy()
    # npm frontend scripts invoke `python`; pin that name to the interpreter
    # running this release build instead of whichever legacy system Python
    # happens to appear first on PATH.
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(ROOT), env.get("PYTHONPATH", "")) if value
    )
    with tempfile.TemporaryDirectory(prefix="argus-python-") as shim_dir:
        shim = Path(shim_dir) / ("python.cmd" if os.name == "nt" else "python")
        if os.name == "nt":
            # Keep the batch file ASCII-only. cmd.exe decodes .cmd files with
            # the active OEM code page, so embedding a Unicode checkout path
            # here corrupts it before Python can start.
            env["ARGUS_RELEASE_PYTHON"] = sys.executable
            shim.write_text('@"%ARGUS_RELEASE_PYTHON%" %*\n', encoding="ascii")
        else:
            # Following a venv's interpreter symlink can lose its pyvenv.cfg.
            shim.write_text(
                f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
        env["PATH"] = os.pathsep.join((shim_dir, env.get("PATH", "")))
        subprocess.run(argv, cwd=cwd, check=True, env=env)


def main() -> int:
    try:
        # Bundled verticals may ship independent workbench frontends. Build each
        # before the release digest, keeping domain code out of the host UI.
        for manifest_path in sorted((ROOT / "argus_skill" / "verticals").glob("*/workbench.json")):
            spec = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not spec.get("frontend"):
                continue
            frontend = (manifest_path.parent / spec["frontend"]).resolve()
            if manifest_path.parent.resolve() not in frontend.parents:
                raise ValueError("vertical frontend must remain inside its package")
            if not (frontend / "node_modules").is_dir():
                run(NPM_COMMAND, "ci", cwd=frontend)
            run(NPM_COMMAND, "run", "build", cwd=frontend)
        run(sys.executable, "-m", "argus_skill.release_tools.build_plugins")
        # Generated protocol source participates in the release digest, so it
        # must be refreshed before computing the manifest. Reversing these two
        # steps makes a schema change require two builds: the first build updates
        # types and then correctly rejects its now-stale manifest.
        run(
            sys.executable,
            "-m",
            "argus_skill.release_tools.generate_event_types",
        )
        run(
            sys.executable,
            "-m",
            "argus_skill.release_tools.generate_event_fixtures",
        )
        run(
            sys.executable,
            "-m",
            "argus_skill.release_tools.generate_resource_status",
        )
        run(
            sys.executable,
            "-m",
            "argus_skill.release_tools.generate_manifest",
            "--prepare-build",
        )
        run(NPM_COMMAND, "run", "build", cwd=ROOT / "frontend" / "web")
        run(NPM_COMMAND, "run", "build", cwd=ROOT / "frontend" / "tui")
        run(
            sys.executable,
            "-m",
            "argus_skill.release_tools.check_artifacts",
        )
    except subprocess.CalledProcessError as exc:
        return int(exc.returncode or 1)
    manifest = json.loads((ROOT / "argus_skill" / "release_manifest.json").read_text())
    print(f"release ready: {manifest['package_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
