#!/usr/bin/env python3
"""Fail closed unless npm tarballs contain exactly the public release files."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path, PurePosixPath

EXPECTED_FILES = {
    "@argusevolve/argus": {
        "package/package.json",
        "package/README.md",
        "package/bin/argus.mjs",
        "package/bin/argus-skill.mjs",
        "package/bin/launcher.mjs",
    },
    "@argusevolve/argus-linux-x64": {
        "package/package.json",
        "package/README.md",
        "package/THIRD_PARTY_NOTICES.txt",
        "package/bin/argus-core",
        "package/bin/argus-core.sha256",
    },
    "@argusevolve/argus-win32-x64": {
        "package/package.json",
        "package/README.md",
        "package/THIRD_PARTY_NOTICES.txt",
        "package/bin/argus-core.exe",
        "package/bin/argus-core.exe.sha256",
    },
}

FORBIDDEN_PARTS = {
    ".git",
    ".github",
    "docs",
    "tests",
    "vc_materials",
    "bp_figures",
    "technical_report",
}
FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyo", ".ts", ".tsx", ".map"}


def _member_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.getmember(name)
    handle = archive.extractfile(member)
    if handle is None:
        raise RuntimeError(f"unable to read {name}")
    return handle.read()


def verify(path: Path) -> tuple[str, str]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if any(not member.isfile() and not member.isdir() for member in members):
            raise RuntimeError(f"{path.name}: links or special files are forbidden")
        names = {member.name for member in members if member.isfile()}
        for name in names:
            pure = PurePosixPath(name)
            if set(pure.parts) & FORBIDDEN_PARTS:
                raise RuntimeError(f"{path.name}: forbidden private path {name}")
            if pure.suffix.lower() in FORBIDDEN_SUFFIXES:
                raise RuntimeError(f"{path.name}: forbidden source file {name}")

        package = json.loads(
            _member_bytes(archive, "package/package.json").decode("utf-8")
        )
        package_name = str(package.get("name") or "")
        version = str(package.get("version") or "")
        expected = EXPECTED_FILES.get(package_name)
        if expected is None:
            raise RuntimeError(f"{path.name}: unexpected package {package_name!r}")
        if names != expected:
            missing = sorted(expected - names)
            extra = sorted(names - expected)
            raise RuntimeError(
                f"{path.name}: package whitelist mismatch; missing={missing}, extra={extra}"
            )
        if package.get("license") != "UNLICENSED":
            raise RuntimeError(f"{path.name}: binary preview must stay UNLICENSED")
        if not version:
            raise RuntimeError(f"{path.name}: version is empty")
        if "-beta." not in version:
            raise RuntimeError(f"{path.name}: version is not an npm beta")
        return package_name, version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tarballs", type=Path, nargs="+")
    args = parser.parse_args()
    found: dict[str, str] = {}
    for raw in args.tarballs:
        path = raw.expanduser().resolve()
        name, version = verify(path)
        if name in found:
            raise SystemExit(f"duplicate npm package tarball: {name}")
        found[name] = version
        print(f"verified {name}@{version}: {path.name}")
    versions = set(found.values())
    if len(versions) != 1:
        raise SystemExit(f"npm tarball versions disagree: {found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
