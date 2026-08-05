from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.materialize_npm_release import materialize
from scripts.verify_npm_tarballs import LAUNCHER_FILES, PLATFORM_FILES

_VERSION = "0.1.2-beta.g0123456789ab"


def _tarball(tmp_path: Path, version: str, *, corrupt_digest: bool = False) -> Path:
    variant = next(
        (name for name in PLATFORM_FILES if version.endswith(f"-{name}")),
        None,
    )
    expected = PLATFORM_FILES[variant] if variant else LAUNCHER_FILES
    files = {name: b"placeholder" for name in expected}
    files["package/package.json"] = json.dumps(
        {"name": "@argusevolve/argus", "version": version, "license": "UNLICENSED"}
    ).encode()
    if variant:
        binary_name = "argus-core.exe" if variant == "win32-x64" else "argus-core"
        binary = f"binary:{variant}".encode()
        digest = hashlib.sha256(binary).hexdigest()
        files[f"package/bin/{binary_name}"] = binary
        files[f"package/bin/{binary_name}.sha256"] = (
            f"{'0' * 64 if corrupt_digest else digest}  {binary_name}\n".encode()
        )
        files["package/THIRD_PARTY_NOTICES.txt"] = f"notices:{variant}".encode()
    path = tmp_path / f"{version}.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def _set(tmp_path: Path, *, corrupt_linux: bool = False) -> list[Path]:
    return [
        _tarball(tmp_path, _VERSION),
        _tarball(
            tmp_path,
            f"{_VERSION}-linux-x64",
            corrupt_digest=corrupt_linux,
        ),
        _tarball(tmp_path, f"{_VERSION}-win32-x64"),
    ]


def test_materialize_uses_canonical_tarball_binaries_and_renames_hashes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "assets"

    written = materialize(_set(tmp_path), version=_VERSION, output=output)

    assert len(written) == 6
    linux = output / f"argus-{_VERSION}-linux-x64"
    windows = output / f"argus-{_VERSION}-win32-x64.exe"
    assert linux.read_bytes() == b"binary:linux-x64"
    assert windows.read_bytes() == b"binary:win32-x64"
    assert (output / "THIRD_PARTY_NOTICES-linux.txt").read_bytes() == (
        b"notices:linux-x64"
    )
    assert (output / "THIRD_PARTY_NOTICES-windows.txt").read_bytes() == (
        b"notices:win32-x64"
    )
    digest = hashlib.sha256(linux.read_bytes()).hexdigest()
    assert (output / f"{linux.name}.sha256").read_text().strip() == (
        f"{digest}  {linux.name}"
    )


def test_materialize_rejects_corrupt_packaged_binary_digest(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        materialize(
            _set(tmp_path, corrupt_linux=True),
            version=_VERSION,
            output=tmp_path / "assets",
        )
