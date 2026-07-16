from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.verify_npm_tarballs import LAUNCHER_FILES, PLATFORM_FILES, verify


def _tarball(
    tmp_path: Path,
    version: str,
    *,
    extras: dict[str, bytes] | None = None,
) -> Path:
    path = tmp_path / "package.tgz"
    variant = next(
        (item for item in PLATFORM_FILES if version.endswith(f"-{item}")), None
    )
    expected_files = PLATFORM_FILES[variant] if variant else LAUNCHER_FILES
    files = {name: b"placeholder" for name in expected_files}
    files["package/package.json"] = json.dumps({
        "name": "@argusevolve/argus",
        "version": version,
        "license": "UNLICENSED",
    }).encode()
    files.update(extras or {})
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_valid_launcher_tarball_passes_exact_whitelist(tmp_path: Path) -> None:
    version = "0.1.2-beta.g0123456789ab"
    assert verify(_tarball(tmp_path, version)) == (
        "@argusevolve/argus",
        version,
    )


def test_valid_platform_variant_uses_the_same_package_name(tmp_path: Path) -> None:
    version = "0.1.2-beta.g0123456789ab-linux-x64"
    assert verify(_tarball(tmp_path, version)) == (
        "@argusevolve/argus",
        version,
    )


def test_private_document_path_is_rejected(tmp_path: Path) -> None:
    path = _tarball(
        tmp_path,
        "0.1.2-beta.g0123456789ab",
        extras={"package/docs/商业计划书.md": b"private"},
    )
    with pytest.raises(RuntimeError, match="forbidden private path"):
        verify(path)


def test_source_file_is_rejected(tmp_path: Path) -> None:
    path = _tarball(
        tmp_path,
        "0.1.2-beta.g0123456789ab-linux-x64",
        extras={"package/bin/private_core.py": b"print('no')"},
    )
    with pytest.raises(RuntimeError, match="forbidden source file"):
        verify(path)
