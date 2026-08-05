#!/usr/bin/env python3
"""Install an Argus wheel into a clean venv and prove its product runtime works."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path

try:
    from scripts.verify_binary import _run_web_smoke
except ModuleNotFoundError:  # direct ``python scripts/verify_wheel.py``
    from verify_binary import _run_web_smoke


def _venv_executable(root: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return root / directory / f"{name}{suffix}"


def verify(wheel: Path) -> dict:
    resolved = wheel.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"missing wheel: {resolved}")
    with tempfile.TemporaryDirectory(prefix="argus-wheel-smoke-") as raw:
        root = Path(raw) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(root)
        python = _venv_executable(root, "python")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                str(resolved),
            ],
            check=True,
            timeout=180,
        )
        argus = _venv_executable(root, "argus")
        argus_skill = _venv_executable(root, "argus-skill")
        version = subprocess.run(
            [str(argus), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        rendered = (version.stdout or "") + (version.stderr or "")
        if version.returncode != 0 or "argus-skill" not in rendered:
            raise RuntimeError(
                f"wheel product version smoke failed ({version.returncode}): {rendered}"
            )
        return _run_web_smoke(argus_skill, os.environ.copy())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    try:
        meta = verify(args.wheel)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"wheel smoke failed: {exc}") from exc
    print(
        f"wheel smoke passed: {args.wheel.expanduser().resolve()} · "
        f"{meta['runtime']['release_id']} · clean WebAPI ready"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
