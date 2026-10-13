"""Bounded, project-owned uploads; only completed files become attachments."""
from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import stat
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import ijson

from ..core.file_lock import exclusive_file_lock
from . import attachments as store
from .artifacts import project_workspace

CHUNK_BYTES = 4 * 1024 * 1024
PENDING_BYTES = 2 * 1024 * 1024 * 1024
PENDING_COUNT = 20
IDLE_SECONDS = 24 * 60 * 60
DISK_RESERVE_BYTES = 256 * 1024 * 1024
JSON_TOKEN_BYTES = 4 * 1024 * 1024
_PART = ".upload.part"
_PENDING = ".upload.json"
_CONTROL = re.compile(r"[\x00-\x07\x0b\x0e-\x1f]")
_JSON_DELIMITERS = re.compile(rb'["\\{}\[\],:\s]')
_JSON_BOUNDARY = re.compile(rb'[{}\[\],:\s]')


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class _Directory:
    """Use the existing POSIX no-follow / Windows guarded storage boundary."""

    def __init__(self, location: int | Path):
        self.location = location

    @contextmanager
    def file(self, name: str, mode: str = "rb"):
        store._validate_storage_component(name)
        with ExitStack() as stack:
            if isinstance(self.location, Path):
                path = self.location / name
                if mode != "xb":
                    stack.enter_context(store._windows_guard_path(path, directory=False))
                handle = stack.enter_context(path.open(mode))
            else:
                flags = store._FILE_READ_FLAGS
                if mode == "xb":
                    flags = store._FILE_WRITE_FLAGS
                elif mode == "r+b":
                    flags = (flags & ~os.O_RDONLY) | os.O_RDWR
                descriptor = os.open(name, flags, 0o600, dir_fd=self.location)
                handle = stack.enter_context(os.fdopen(descriptor, mode))
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise UploadError("attachment payload must be a regular file")
            if os.fstat(handle.fileno()).st_nlink != 1:
                raise UploadError("attachment payload must not be a hard link")
            yield handle

    def read(self, name: str) -> dict[str, Any]:
        with self.file(name) as handle:
            raw = handle.read(store._ATTACHMENT_METADATA_MAX_BYTES + 1)
        if len(raw) > store._ATTACHMENT_METADATA_MAX_BYTES:
            raise UploadError("upload metadata is too large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise UploadError("invalid upload metadata")
        return result

    def write(self, name: str, data: dict[str, Any]) -> None:
        content = (json.dumps(data, ensure_ascii=False) + "\n").encode()
        if isinstance(self.location, Path):
            store._windows_write_file_atomic(self.location, name, content)
        else:
            store._write_file_atomic(self.location, name, content)

    def exists(self, name: str) -> bool:
        try:
            if isinstance(self.location, Path):
                (self.location / name).lstat()
            else:
                os.stat(name, dir_fd=self.location, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def rename(self, source: str, target: str) -> None:
        if isinstance(self.location, Path):
            os.replace(self.location / source, self.location / target)
        else:
            os.replace(source, target, src_dir_fd=self.location, dst_dir_fd=self.location)
            store._fsync_directory(self.location)


@contextmanager
def _directory(workspace: Path, sid: str, attachment_id: str | None = None, *, create=False):
    store._validate_storage_component(sid)
    if attachment_id is not None and not store._ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise UploadError("invalid attachment_id")
    store._require_secure_attachment_storage()
    with ExitStack() as stack:
        if os.name == "nt":
            location = store._windows_open_attachment_session_root(workspace, sid, create=create, stack=stack)
            if attachment_id:
                location = location / attachment_id
                if create:
                    location.mkdir()
                stack.enter_context(store._windows_guard_path(location, directory=True))
        else:
            location = store._open_attachment_session_root(workspace, sid, create=create)
            stack.callback(os.close, location)
            if attachment_id:
                location = store._open_storage_directory(
                    location, attachment_id, display_path=attachment_id, create=create, must_create=create,
                )
                stack.callback(os.close, location)
        yield _Directory(location)


def _remove(workspace: Path, sid: str, attachment_id: str) -> None:
    with _directory(workspace, sid) as directory:
        if isinstance(directory.location, Path):
            store._windows_remove_tree_nofollow(directory.location / attachment_id)
        else:
            store._remove_tree_nofollow(directory.location, attachment_id, display_path=attachment_id)


class _JsonReader:
    """Bound lexer allocation before handing syntax validation to ijson."""

    def __init__(self, handle, cancelled: Callable[[], bool] | None = None):
        self.handle = handle
        self.cancelled = cancelled
        self.quoted = False
        self.escaped = False
        self.token_bytes = 0

    def read(self, size: int = -1) -> bytes:
        if self.cancelled and self.cancelled():
            raise UploadError("upload validation interrupted; retry or cancel the upload", 409)
        data = self.handle.read(min(size, 64 * 1024) if size >= 0 else 64 * 1024)
        if not self.quoted and b'"' not in data:
            # Interior tokens in this 64 KiB buffer cannot exceed the cap.
            # Only a token crossing read boundaries needs a running count.
            boundary = _JSON_BOUNDARY.search(data)
            self.token_bytes += boundary.start() if boundary else len(data)
            self._check()
            if boundary:
                last = max(data.rfind(char) for char in (b"{", b"}", b"[", b"]", b",", b":", b" ", b"\t", b"\n", b"\r", b"\f"))
                self.token_bytes = len(data) - last - 1
            return data
        last = 0
        for match in _JSON_DELIMITERS.finditer(data):
            gap = match.start() - last
            self.token_bytes += gap
            if gap:
                self.escaped = False
            self._check()
            char = match.group()
            if self.quoted:
                self.token_bytes += 1
                if self.escaped:
                    self.escaped = False
                elif char == b"\\":
                    self.escaped = True
                elif char == b'"':
                    self.quoted = False
                    self.token_bytes = 0
            elif char == b'"':
                self.quoted = True
                self.token_bytes = 0
            else:
                self.token_bytes = 0
            last = match.end()
        self.token_bytes += len(data) - last
        if last < len(data):
            self.escaped = False
        self._check()
        return data

    def _check(self):
        if self.token_bytes > JSON_TOKEN_BYTES:
            raise UploadError("JSON contains a value/key longer than 4 MiB; split long values before uploading")


def _validate_file(handle, metadata: dict[str, Any], cancelled: Callable[[], bool] | None = None) -> None:
    name = metadata["original_name"]
    suffix = Path(name).suffix.lower()
    handle.seek(0)
    if suffix in store._BINARY_UPLOAD_SPECS:
        store._validate_attachment_payload(
            suffix=suffix, declared_mime=metadata["mime"], content=handle.read(16), original_name=name,
        )
        return
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        while chunk := handle.read(64 * 1024):
            if cancelled and cancelled():
                raise UploadError("upload validation interrupted; retry or cancel the upload", 409)
            if _CONTROL.search(decoder.decode(chunk)):
                raise UploadError(f"{name} contains unsupported binary control bytes")
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadError(f"{name} must be UTF-8 text") from exc
    if suffix == ".json":
        handle.seek(0)
        if handle.read(3) != codecs.BOM_UTF8:
            handle.seek(0)
        try:
            depth = 0
            for event, _value in ijson.basic_parse(_JsonReader(handle, cancelled), use_float=False):
                if event in {"start_map", "start_array"}:
                    depth += 1
                    if depth > 128:
                        raise UploadError("JSON nesting exceeds the 128-level upload limit")
                elif event in {"end_map", "end_array"}:
                    depth -= 1
        except ijson.JSONError as exc:
            raise UploadError(f"{name} is not valid JSON") from exc


class AttachmentUploads:
    def __init__(self, roots: list[Path]):
        self.roots = roots
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)

    @contextmanager
    def _locked(self, root: Path) -> Iterator[None]:
        # One admission/commit owner per instance, including separate workers.
        if not self.lock.acquire(timeout=1):
            raise UploadError("upload storage is busy; retry shortly", 429)
        try:
            with (root / ".attachment-uploads.lock").open("a") as handle:
                with exclusive_file_lock(handle, timeout_seconds=1, lock_name="attachment uploads"):
                    yield
        finally:
            self.lock.release()

    def _pending(self) -> list[tuple[Path, str, str, dict[str, Any]]]:
        pending = []
        seen: set[str] = set()
        for root in self.roots:
            for project in (root / "projects").glob("*"):
                sid = project.name
                if sid in seen or not project.is_dir():
                    continue
                seen.add(sid)
                workspace = project_workspace(sid, global_root=root)
                if workspace is None:
                    continue
                try:
                    with _directory(workspace, sid) as parent:
                        names = os.listdir(parent.location)
                except FileNotFoundError:
                    continue
                for attachment_id in names:
                    if not store._ATTACHMENT_ID_RE.fullmatch(attachment_id):
                        continue
                    with _directory(workspace, sid, attachment_id) as directory:
                        if directory.exists("metadata.json") or not directory.exists(_PENDING):
                            continue
                        metadata = directory.read(_PENDING)
                        store._validate_metadata_payload(metadata, sid, attachment_id)
                        pending.append((workspace, sid, attachment_id, metadata))
        return pending

    def _cleanup(self) -> list[tuple[Path, str, str, dict[str, Any]]]:
        live = []
        now = time.time()
        for workspace, sid, attachment_id, metadata in self._pending():
            if now - float(metadata["updated_at"]) >= IDLE_SECONDS:
                _remove(workspace, sid, attachment_id)
            else:
                live.append((workspace, sid, attachment_id, metadata))
        return live

    def cleanup(self) -> None:
        if not self.roots:
            return
        with self._locked(self.roots[0]):
            self._cleanup()

    def begin(self, root: Path, sid: str, name: str, mime: str, size: int) -> dict[str, Any]:
        workspace = project_workspace(sid, global_root=root)
        if workspace is None:
            raise UploadError("project workspace is unavailable", 404)
        name = store._normalize_client_filename(name)
        suffix = Path(name).suffix.lower()
        specs = {**store._TEXT_UPLOAD_SPECS, **store._BINARY_UPLOAD_SPECS}
        if suffix not in specs:
            raise UploadError("unsupported attachment type")
        canonical, allowed = specs[suffix]
        if mime and mime.split(";", 1)[0].strip().lower() not in allowed:
            raise UploadError(f"{name} declared unsupported MIME")
        if not 0 <= size <= store.MESSAGE_ATTACHMENT_MAX_BYTES:
            raise UploadError("single file exceeds the 100 MiB limit", 413)
        with self._locked(self.roots[0]):
            pending = self._cleanup()
            reserved = sum(item[3]["size_bytes"] for item in pending)
            project_pending = [item for item in pending if item[1] == sid]
            if (len(pending) >= PENDING_COUNT or reserved + size > PENDING_BYTES
                    or len(project_pending) >= store.MESSAGE_ATTACHMENT_MAX_COUNT
                    or sum(item[3]["size_bytes"] for item in project_pending) + size > store.MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES):
                raise UploadError("temporary upload quota is full; cancel pending uploads or wait for cleanup", 429)
            if shutil.disk_usage(workspace).free < reserved + size + DISK_RESERVE_BYTES:
                raise UploadError("insufficient disk space for this upload", 507)
            attachment_id = f"att-{uuid4().hex[:12]}"
            stored_name = store._sanitize_storage_name(name)
            metadata = {
                "schema_version": store.ATTACHMENT_SCHEMA_VERSION,
                "session_id": sid, "attachment_id": attachment_id,
                "original_name": name, "stored_name": stored_name, "mime": canonical,
                "relative_path": store._attachment_payload_relative_path(sid, attachment_id, stored_name),
                "size_bytes": size, "created_at": time.time(), "updated_at": time.time(),
            }
            created = False
            try:
                with _directory(workspace, sid, attachment_id, create=True) as directory:
                    created = True
                    directory.write(_PENDING, metadata)
                    with directory.file(_PART, "xb"):
                        pass
            except (OSError, ValueError):
                if created:
                    _remove(workspace, sid, attachment_id)
                raise
            return {"attachment_id": attachment_id, "offset": 0, "size_bytes": size, "complete": False}

    def operate(self, root: Path, sid: str, attachment_id: str, action: str, *,
                offset: int = 0, content: bytes = b"", cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        workspace = project_workspace(sid, global_root=root)
        if workspace is None:
            raise UploadError("project workspace is unavailable", 404)
        with self._locked(self.roots[0]):
            with _directory(workspace, sid, attachment_id) as directory:
                if directory.exists("metadata.json"):
                    metadata = directory.read("metadata.json")
                    store._validate_metadata_payload(metadata, sid, attachment_id)
                    if action == "append":
                        raise UploadError("upload is already complete", 409)
                    return {"attachment_id": attachment_id, "offset": metadata["size_bytes"],
                            "size_bytes": metadata["size_bytes"], "complete": True, "attachment": metadata}
                metadata = directory.read(_PENDING)
                store._validate_metadata_payload(metadata, sid, attachment_id)
                if action == "cancel":
                    result = {"cancelled": True}
                else:
                    # A crash after rename but before metadata commit is recoverable.
                    payload = _PART if directory.exists(_PART) else metadata["stored_name"]
                    with directory.file(payload, "r+b" if action in {"append", "complete"} else "rb") as handle:
                        actual = os.fstat(handle.fileno()).st_size
                        if action == "append":
                            if offset != actual:
                                raise UploadError(f"upload offset conflict; expected {actual}", 409)
                            if not content or len(content) > CHUNK_BYTES or actual + len(content) > metadata["size_bytes"]:
                                raise UploadError("chunk exceeds the declared file size or 4 MiB request limit", 413)
                            if shutil.disk_usage(workspace).free < len(content) + DISK_RESERVE_BYTES:
                                raise UploadError("insufficient disk space for this chunk", 507)
                            handle.seek(actual)
                            handle.write(content)
                            handle.flush()
                            os.fsync(handle.fileno())
                            actual = handle.tell()
                        elif action == "complete":
                            if actual != metadata["size_bytes"]:
                                raise UploadError(f"upload is incomplete; received {actual} bytes", 409)
                            _validate_file(handle, metadata, cancelled)
                            if cancelled and cancelled():
                                raise UploadError("upload validation interrupted; retry or cancel the upload", 409)
                            os.fsync(handle.fileno())
                        elif action != "status":
                            raise UploadError("unknown upload operation")
                    metadata["updated_at"] = time.time()
                    if action == "complete":
                        if payload == _PART:
                            directory.rename(_PART, metadata["stored_name"])
                        metadata.pop("updated_at", None)
                        directory.write("metadata.json", metadata)
                        return {"attachment_id": attachment_id, "offset": actual, "size_bytes": actual,
                                "complete": True, "attachment": metadata}
                    directory.write(_PENDING, metadata)
                    return {"attachment_id": attachment_id, "offset": actual,
                            "size_bytes": metadata["size_bytes"], "complete": False}
            _remove(workspace, sid, attachment_id)
            return result
