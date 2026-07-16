#!/usr/bin/env python3
"""Fail closed unless npm tarballs contain exactly the public release files."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

PACKAGE_NAME = "@argusevolve/argus"
LAUNCHER_FILES = {
    "package/package.json",
    "package/README.md",
    "package/bin/argus.mjs",
    "package/bin/argus-skill.mjs",
    "package/bin/launcher.mjs",
}
PLATFORM_FILES = {
    "linux-x64": {
        "package/package.json",
        "package/README.md",
        "package/THIRD_PARTY_NOTICES.txt",
        "package/bin/argus-core",
        "package/bin/argus-core.sha256",
    },
    "win32-x64": {
        "package/package.json",
        "package/README.md",
        "package/THIRD_PARTY_NOTICES.txt",
        "package/bin/argus-core.exe",
        "package/bin/argus-core.exe.sha256",
    },
}


def _variant(version: str) -> str | None:
    for variant in PLATFORM_FILES:
        if version.endswith(f"-{variant}"):
            return variant
    return None


def _base_version(version: str) -> str:
    variant = _variant(version)
    return version[: -(len(variant) + 1)] if variant else version

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
VERSION_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+-beta\.g[0-9a-f]{12}"
    r"(?:-(?:linux|win32)-x64)?$"
)


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
        if package_name != PACKAGE_NAME:
            raise RuntimeError(f"{path.name}: unexpected package {package_name!r}")
        variant = _variant(version)
        expected = PLATFORM_FILES[variant] if variant else LAUNCHER_FILES
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
        if not VERSION_RE.fullmatch(version):
            raise RuntimeError(f"{path.name}: version is not commit-derived beta SemVer")
        return package_name, version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tarballs", type=Path, nargs="+")
    args = parser.parse_args()
    found: set[tuple[str, str]] = set()
    base_versions: set[str] = set()
    for raw in args.tarballs:
        path = raw.expanduser().resolve()
        name, version = verify(path)
        key = (name, version)
        if key in found:
            raise SystemExit(f"duplicate npm package tarball: {name}@{version}")
        found.add(key)
        base_versions.add(_base_version(version))
        print(f"verified {name}@{version}: {path.name}")
    if len(base_versions) != 1:
        raise SystemExit(f"npm tarball base versions disagree: {sorted(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
