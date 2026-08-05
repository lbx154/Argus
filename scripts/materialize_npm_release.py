#!/usr/bin/env python3
"""Materialize GitHub release assets from canonical npm package tarballs.

A recovery run may rebuild a different PyInstaller byte stream for an immutable
npm version.  GitHub assets must therefore come from the exact tarballs stored in
npm, not from a retry's fresh build.
"""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path

try:
    from scripts.verify_npm_tarballs import verify
except ModuleNotFoundError:  # direct ``python scripts/materialize_npm_release.py``
    from verify_npm_tarballs import verify

_VARIANTS = {
    "linux-x64": ("argus-core", False, "linux"),
    "win32-x64": ("argus-core.exe", True, "windows"),
}


def _member_bytes(path: Path, name: str) -> bytes:
    with tarfile.open(path, mode="r:gz") as archive:
        member = archive.getmember(name)
        handle = archive.extractfile(member)
        if handle is None:
            raise RuntimeError(f"unable to read {name} from {path.name}")
        return handle.read()


def materialize(
    tarballs: list[Path],
    *,
    version: str,
    output: Path,
) -> list[Path]:
    by_version: dict[str, Path] = {}
    for raw in tarballs:
        path = raw.expanduser().resolve()
        _name, package_version = verify(path)
        if package_version in by_version:
            raise RuntimeError(f"duplicate canonical tarball for {package_version}")
        by_version[package_version] = path

    expected_versions = {
        version,
        f"{version}-linux-x64",
        f"{version}-win32-x64",
    }
    if set(by_version) != expected_versions:
        raise RuntimeError(
            "canonical npm package set mismatch: "
            f"missing={sorted(expected_versions - set(by_version))}, "
            f"extra={sorted(set(by_version) - expected_versions)}"
        )

    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for variant, (binary_name, windows, notice_label) in _VARIANTS.items():
        tarball = by_version[f"{version}-{variant}"]
        binary = _member_bytes(tarball, f"package/bin/{binary_name}")
        packaged_digest = _member_bytes(
            tarball, f"package/bin/{binary_name}.sha256"
        ).decode("utf-8").strip()
        digest = hashlib.sha256(binary).hexdigest()
        if packaged_digest != f"{digest}  {binary_name}":
            raise RuntimeError(f"packaged SHA-256 mismatch in {tarball.name}")

        release_name = (
            f"argus-{version}-win32-x64.exe"
            if windows
            else f"argus-{version}-linux-x64"
        )
        binary_path = output / release_name
        binary_path.write_bytes(binary)
        if not windows:
            binary_path.chmod(0o755)
        digest_path = output / f"{release_name}.sha256"
        digest_path.write_text(
            f"{digest}  {release_name}\n",
            encoding="utf-8",
        )
        notices_path = output / f"THIRD_PARTY_NOTICES-{notice_label}.txt"
        notices_path.write_bytes(
            _member_bytes(tarball, "package/THIRD_PARTY_NOTICES.txt")
        )
        written.extend((binary_path, digest_path, notices_path))
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("tarballs", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        assets = materialize(
            args.tarballs,
            version=args.version,
            output=args.output.expanduser().resolve(),
        )
    except (OSError, RuntimeError, tarfile.TarError) as exc:
        raise SystemExit(f"npm release materialization failed: {exc}") from exc
    print(f"materialized {len(assets)} canonical release assets in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
