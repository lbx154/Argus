#!/usr/bin/env python3
"""Build one native Argus executable without shipping Python source files."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "binary" / "argus.spec"


def _run(argv: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def _write_third_party_notices(output: Path) -> Path:
    result = _run(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--format=plain-vertical",
            "--with-license-file",
            "--no-license-path",
        ],
        capture=True,
    )
    path = output / "THIRD_PARTY_NOTICES.txt"
    path.write_text(
        "Argus binary third-party notices\n"
        "==================================\n\n"
        + result.stdout,
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist-binary")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    work = ROOT / ".pyinstaller"
    output.mkdir(parents=True, exist_ok=True)

    _run([sys.executable, "scripts/check_release_artifacts.py"])
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(output),
            "--workpath",
            str(work),
            str(SPEC),
        ]
    )

    binary = output / ("argus-core.exe" if os.name == "nt" else "argus-core")
    if not binary.is_file():
        raise RuntimeError(f"PyInstaller did not produce {binary}")
    binary.chmod(binary.stat().st_mode | 0o111)

    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (output / f"{binary.name}.sha256").write_text(
        f"{digest}  {binary.name}\n", encoding="utf-8"
    )
    notices = _write_third_party_notices(output)
    print(f"binary ready: {binary}")
    print(f"sha256: {digest}")
    print(f"notices: {notices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
