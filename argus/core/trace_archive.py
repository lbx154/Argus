"""Verified, immutable ZIP archives for research traces.

Callers serialize writers and reclamation with a portable file lock. Publishing
an archive does not delete its source; a failed archive must leave it intact.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def reject_links(path: Path) -> None:
    """Do not archive, restore, or reclaim through symlinks or reparse points."""
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"trace path is a link or reparse point: {candidate}")


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def trace_snapshot(source: Path) -> tuple[tuple, ...]:
    reject_links(source)
    paths = [source]
    if source.is_dir():
        def scan_error(error):
            raise error

        for root, dirs, files in os.walk(source, followlinks=False, onerror=scan_error):
            for name in sorted(dirs + files):
                path = Path(root) / name
                reject_links(path)
                paths.append(path)
    rows = []
    for path in paths:
        info = path.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        if not directory and not stat.S_ISREG(info.st_mode):
            raise OSError(f"unsupported trace entry: {path}")
        name = path.relative_to(source).as_posix() if source.is_dir() else source.name
        rows.append((name, directory, info.st_size, info.st_mtime_ns,
                     info.st_ctime_ns, info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)))
    return tuple(sorted(rows))


def _member_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise OSError(f"unsafe trace archive member: {name}")
    if path.as_posix() != name:
        raise OSError(f"noncanonical trace archive member: {name}")
    return "payload/" + name


def verify_archive(path: Path) -> dict:
    """Read every payload byte, checking sizes, SHA-256 and ZIP CRCs."""
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("kind") not in {"file", "directory"}:
            raise OSError("unsupported trace archive manifest")
        expected = {"manifest.json"}
        for row in manifest["entries"]:
            name = _member_name(row["name"]) + ("/" if row["directory"] else "")
            if name in expected:
                raise OSError("duplicate trace archive member")
            expected.add(name)
            digest = hashlib.sha256()
            size = 0
            with archive.open(name) as payload:
                while chunk := payload.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != row["size"] or digest.hexdigest() != row["sha256"]:
                raise OSError(f"trace archive checksum mismatch: {name}")
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != expected:
            raise OSError("unexpected trace archive members")
    return manifest


@dataclass(frozen=True)
class TraceArchive:
    path: Path
    snapshot: tuple[tuple, ...]


def archive_trace(source: Path, destination: Path) -> TraceArchive:
    """Publish a compressed, verified snapshot; never modify the source."""
    snapshot = trace_snapshot(source)
    ancestor = destination
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    reject_links(ancestor)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_links(destination)
    fd, temporary = tempfile.mkstemp(prefix=".trace-", suffix=".tmp", dir=destination)
    os.close(fd)
    pending = Path(temporary)
    target = destination / f"{time.time_ns():020d}-{uuid.uuid4().hex}.zip"
    try:
        entries = []
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for name, directory, *metadata in snapshot:
                if name == ".":
                    continue
                member = _member_name(name) + ("/" if directory else "")
                digest = hashlib.sha256()
                size = 0
                if directory:
                    archive.writestr(member, b"")
                else:
                    path = source / name if source.is_dir() else source
                    reject_links(path)
                    with path.open("rb") as payload, archive.open(member, "w", force_zip64=True) as output:
                        while chunk := payload.read(1024 * 1024):
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                entries.append({"name": name, "directory": directory, "size": size,
                                "sha256": digest.hexdigest(), "mode": metadata[-1]})
            archive.writestr("manifest.json", json.dumps({
                "schema_version": 1, "kind": "directory" if source.is_dir() else "file",
                "source_name": source.name, "created_at": time.time(), "entries": entries,
            }, ensure_ascii=False))
        verify_archive(pending)
        if trace_snapshot(source) != snapshot:
            raise OSError("trace changed while being archived")
        with pending.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(pending, target)
        # A durable ZIP inside a newly created, non-durable parent directory
        # can still disappear on power loss. Sync the full directory chain,
        # including chains left by an earlier failed archival attempt.
        for directory in (destination, *destination.parents):
            sync_directory(directory)
        return TraceArchive(target, snapshot)
    finally:
        pending.unlink(missing_ok=True)


def restore_trace(archive_path: Path, destination: Path) -> None:
    """Restore a directory archive atomically without overwriting live state."""
    manifest = verify_archive(archive_path)
    if manifest["kind"] != "directory" or manifest["source_name"] != destination.name:
        raise OSError("trace archive does not match the session")
    reject_links(destination.parent)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        staging = Path(temporary) / destination.name
        staging.mkdir(mode=0o700)
        with zipfile.ZipFile(archive_path) as archive:
            for row in manifest["entries"]:
                member = _member_name(row["name"])
                target = staging / row["name"]
                if row["directory"]:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as output, archive.open(member) as payload:
                        shutil.copyfileobj(payload, output)
                        # Keep restored evidence private and retain owner execute.
                        os.chmod(target, 0o600 | (int(row.get("mode", 0)) & 0o100))
                        output.flush()
                        os.fsync(output.fileno())
        for root, _, _ in os.walk(staging, topdown=False):
            os.chmod(root, 0o700)
            sync_directory(Path(root))
        if destination.exists():
            raise FileExistsError(destination)
        staging.rename(destination)
        sync_directory(destination.parent)
