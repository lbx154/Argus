#!/usr/bin/env python3
"""Prepare public npm tarball directories from a private binary build."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARGUS_TEMPLATE = ROOT / "packaging" / "npm" / "argus"
SUPPORTED = {("linux", "x64"), ("win32", "x64")}
BETA_VERSION = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.g[0-9a-f]{12}$"
)


def validate_version(value: str) -> str:
    version = value.strip()
    if not BETA_VERSION.fullmatch(version):
        raise ValueError(
            "version must match <major>.<minor>.<patch>-beta.g<12-char-commit>"
        )
    return version


def platform_version(version: str, platform: str, arch: str) -> str:
    return f"{version}-{platform}-{arch}"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--platform", choices=["linux", "win32"], required=True)
    parser.add_argument("--arch", choices=["x64"], required=True)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist-binary" / "npm")
    args = parser.parse_args()

    target = (args.platform, args.arch)
    if target not in SUPPORTED:
        raise SystemExit(f"unsupported target: {target}")

    try:
        version = validate_version(args.version)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    binary = args.binary.expanduser().resolve()
    notices = args.notices.expanduser().resolve()
    if not binary.is_file() or not notices.is_file():
        raise SystemExit("binary and third-party notices must exist")

    output = args.output.expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    package_name = "@argusevolve/argus"
    packaged_platform_version = platform_version(
        version, args.platform, args.arch
    )
    platform_dir = output / f"argus-{args.platform}-{args.arch}"
    (platform_dir / "bin").mkdir(parents=True)
    binary_name = "argus-core.exe" if args.platform == "win32" else "argus-core"
    shipped_binary = platform_dir / "bin" / binary_name
    shutil.copy2(binary, shipped_binary)
    shipped_binary.chmod(
        shipped_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    digest = hashlib.sha256(shipped_binary.read_bytes()).hexdigest()
    (platform_dir / "bin" / f"{binary_name}.sha256").write_text(
        f"{digest}  {binary_name}\n", encoding="utf-8"
    )
    shutil.copy2(notices, platform_dir / "THIRD_PARTY_NOTICES.txt")
    _write_json(
        platform_dir / "package.json",
        {
            "name": package_name,
            "version": packaged_platform_version,
            "description": f"Argus proprietary binary for {args.platform} {args.arch}.",
            "license": "UNLICENSED",
            "main": f"bin/{binary_name}",
            "os": [args.platform],
            "cpu": [args.arch],
            "files": [
                f"bin/{binary_name}",
                f"bin/{binary_name}.sha256",
                "README.md",
                "THIRD_PARTY_NOTICES.txt",
            ],
            "publishConfig": {"access": "public"},
        },
    )
    (platform_dir / "README.md").write_text(
        "# Argus platform binary\n\n"
        "Installed automatically by `@argusevolve/argus`. This package contains "
        "a proprietary executable, its SHA-256 digest, and third-party notices; "
        "it contains no Argus Python source tree.\n",
        encoding="utf-8",
    )

    argus_dir = output / "argus"
    shutil.copytree(ARGUS_TEMPLATE, argus_dir)
    argus_package = json.loads(
        (argus_dir / "package.json").read_text(encoding="utf-8")
    )
    argus_package["version"] = version
    argus_package["optionalDependencies"] = {
        "@argusevolve/argus-linux-x64": (
            f"npm:@argusevolve/argus@{platform_version(version, 'linux', 'x64')}"
        ),
        "@argusevolve/argus-win32-x64": (
            f"npm:@argusevolve/argus@{platform_version(version, 'win32', 'x64')}"
        ),
    }
    _write_json(argus_dir / "package.json", argus_package)

    print(f"npm platform package: {platform_dir}")
    print(f"npm Argus package: {argus_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
