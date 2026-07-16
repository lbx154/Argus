#!/usr/bin/env python3
"""Verify exact npm package allowlists for the proprietary binary beta."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ARGUS_FILES = {
    "README.md",
    "package.json",
    "bin/argus.mjs",
    "bin/argus-skill.mjs",
    "bin/launcher.mjs",
}


def _files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _verify_exact_files(path: Path, expected: set[str]) -> None:
    actual = _files(path)
    if actual != expected:
        raise SystemExit(
            f"unexpected npm package contents for {path.name}: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("dist-binary/npm"))
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    argus = root / "argus"
    platforms = sorted(path for path in root.glob("argus-*-x64") if path.is_dir())
    if not argus.is_dir() or not platforms:
        raise SystemExit(f"incomplete npm binary package tree: {root}")

    _verify_exact_files(argus, ARGUS_FILES)
    argus_package = json.loads((argus / "package.json").read_text(encoding="utf-8"))
    if argus_package.get("name") != "@argusevolve/argus":
        raise SystemExit("unexpected main package name")

    for platform in platforms:
        platform_package = json.loads(
            (platform / "package.json").read_text(encoding="utf-8")
        )
        if platform_package.get("name") != "@argusevolve/argus":
            raise SystemExit("platform variants must use the single Argus package name")
        binary_rel = str(platform_package["main"])
        digest_rel = f"{binary_rel}.sha256"
        expected_files = {
            "README.md",
            "THIRD_PARTY_NOTICES.txt",
            "package.json",
            binary_rel,
            digest_rel,
        }
        _verify_exact_files(platform, expected_files)
        binary = platform / binary_rel
        if os.name != "nt" and not os.access(binary, os.X_OK):
            raise SystemExit(f"platform binary is not executable: {binary}")
        digest_line = (platform / digest_rel).read_text(encoding="utf-8").strip()
        expected_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        if digest_line != f"{expected_digest}  {binary.name}":
            raise SystemExit(f"SHA-256 mismatch for {binary}")
        platform_os = str(platform_package["os"][0])
        platform_cpu = str(platform_package["cpu"][0])
        alias_name = f"@argusevolve/argus-{platform_os}-{platform_cpu}"
        version = str(platform_package["version"])
        dependency_spec = argus_package.get("optionalDependencies", {}).get(alias_name)
        expected_spec = f"npm:@argusevolve/argus@{version}"
        if dependency_spec != expected_spec:
            raise SystemExit(
                f"launcher/platform alias mismatch: expected {expected_spec}, "
                f"found {dependency_spec}"
            )
    print(f"npm binary package verification passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
