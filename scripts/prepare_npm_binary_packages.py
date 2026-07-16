#!/usr/bin/env python3
"""Prepare public npm tarball directories from a private binary build."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_TEMPLATE = ROOT / "packaging" / "npm" / "cli"
SUPPORTED = {("linux", "x64"), ("win32", "x64")}


def _version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--platform", choices=["linux", "win32"], required=True)
    parser.add_argument("--arch", choices=["x64"], required=True)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist-binary" / "npm")
    args = parser.parse_args()

    target = (args.platform, args.arch)
    if target not in SUPPORTED:
        raise SystemExit(f"unsupported target: {target}")

    binary = args.binary.expanduser().resolve()
    notices = args.notices.expanduser().resolve()
    if not binary.is_file() or not notices.is_file():
        raise SystemExit("binary and third-party notices must exist")

    version = _version()
    output = args.output.expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    platform_name = f"@argusbot/cli-{args.platform}-{args.arch}"
    platform_dir = output / f"cli-{args.platform}-{args.arch}"
    (platform_dir / "bin").mkdir(parents=True)
    binary_name = "argus-core.exe" if args.platform == "win32" else "argus-core"
    shipped_binary = platform_dir / "bin" / binary_name
    shutil.copy2(binary, shipped_binary)
    shipped_binary.chmod(shipped_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shutil.copy2(notices, platform_dir / notices.name)
    _write_json(
        platform_dir / "package.json",
        {
            "name": platform_name,
            "version": version,
            "description": f"Argus proprietary binary for {args.platform} {args.arch}.",
            "license": "UNLICENSED",
            "main": f"bin/{binary_name}",
            "os": [args.platform],
            "cpu": [args.arch],
            "files": [f"bin/{binary_name}", notices.name],
            "publishConfig": {"access": "public"},
        },
    )
    (platform_dir / "README.md").write_text(
        "# Argus platform binary\n\n"
        "This package is installed automatically by `@argusbot/cli`. "
        "It contains a proprietary executable and no Python source tree.\n",
        encoding="utf-8",
    )

    cli_dir = output / "cli"
    shutil.copytree(CLI_TEMPLATE, cli_dir)
    cli_package = json.loads((cli_dir / "package.json").read_text(encoding="utf-8"))
    cli_package["version"] = version
    cli_package["optionalDependencies"] = {
        "@argusbot/cli-linux-x64": version,
        "@argusbot/cli-win32-x64": version,
    }
    _write_json(cli_dir / "package.json", cli_package)

    print(f"npm platform package: {platform_dir}")
    print(f"npm launcher package: {cli_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
