from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.verify_npm_tarballs import EXPECTED_FILES, verify


def _tarball(
    tmp_path: Path,
    package_name: str,
    *,
    extras: dict[str, bytes] | None = None,
) -> Path:
    path = tmp_path / "package.tgz"
    files = {
        name: b"placeholder"
        for name in EXPECTED_FILES[package_name]
    }
    files["package/package.json"] = json.dumps({
        "name": package_name,
        "version": "0.1.2-beta.1",
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
    assert verify(_tarball(tmp_path, "@argusevolve/argus")) == (
        "@argusevolve/argus",
        "0.1.2-beta.1",
    )


def test_private_document_path_is_rejected(tmp_path: Path) -> None:
    path = _tarball(
        tmp_path,
        "@argusevolve/argus",
        extras={"package/docs/商业计划书.md": b"private"},
    )
    with pytest.raises(RuntimeError, match="forbidden private path"):
        verify(path)


def test_source_file_is_rejected(tmp_path: Path) -> None:
    path = _tarball(
        tmp_path,
        "@argusevolve/argus-linux-x64",
        extras={"package/bin/private_core.py": b"print('no')"},
    )
    with pytest.raises(RuntimeError, match="forbidden source file"):
        verify(path)
