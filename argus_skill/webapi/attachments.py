"""Session-scoped operator attachment uploads stored in the project workdir.

Uploaded bytes live under the canonical execution workspace so every role sees
the same files the Web operator attached. Attachment references stay scoped to
one session id to prevent cross-session reuse, even when sessions share a
workspace.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import time
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from .artifacts import project_workspace

ATTACHMENT_SCHEMA_VERSION = 1
ATTACHMENT_ROOT = Path(".argus") / "attachments"
MESSAGE_ATTACHMENT_MAX_COUNT = 5
MESSAGE_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES = 25 * 1024 * 1024

_ATTACHMENT_IO_CHUNK_BYTES = 64 * 1024
_ATTACHMENT_METADATA_MAX_BYTES = 256 * 1024
_BINARY_UPLOAD_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    ".png": ("image/png", ("image/png", "application/octet-stream")),
    ".jpg": ("image/jpeg", ("image/jpeg", "image/jpg", "application/octet-stream")),
    ".jpeg": ("image/jpeg", ("image/jpeg", "image/jpg", "application/octet-stream")),
    ".webp": ("image/webp", ("image/webp", "application/octet-stream")),
    ".pdf": ("application/pdf", ("application/pdf", "application/octet-stream")),
}
_TEXT_UPLOAD_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    ".md": ("text/markdown", ("text/markdown", "text/plain", "application/octet-stream")),
    ".markdown": ("text/markdown", ("text/markdown", "text/plain", "application/octet-stream")),
    ".txt": ("text/plain", ("text/plain", "application/octet-stream")),
    ".json": ("application/json", ("application/json", "text/plain", "application/octet-stream")),
    ".csv": (
        "text/csv",
        ("text/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream"),
    ),
}
_SUPPORTED_SUFFIXES = frozenset({*_BINARY_UPLOAD_SPECS, *_TEXT_UPLOAD_SPECS})
_ATTACHMENT_ID_RE = re.compile(r"^att-[a-f0-9]{12}$")
_CLIENT_FILENAME_RE = re.compile(r"[^\w .()\-]+", re.UNICODE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


@dataclass(frozen=True)
class _PreparedAttachment:
    attachment_id: str
    original_name: str
    stored_name: str
    suffix: str
    mime: str
    size_bytes: int
    content: bytes


def attachment_limits() -> dict[str, int]:
    return {
        "max_count": MESSAGE_ATTACHMENT_MAX_COUNT,
        "max_bytes_per_file": MESSAGE_ATTACHMENT_MAX_BYTES,
        "max_total_bytes": MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES,
    }


def upload_attachments(
    sid: str,
    files: Sequence[tuple[str, str, bytes]],
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        raise FileNotFoundError(f"unknown project workdir for session {sid}")
    if not files:
        raise ValueError("no attachments were uploaded")
    if len(files) > MESSAGE_ATTACHMENT_MAX_COUNT:
        raise ValueError(
            f"too many attachments; limit is {MESSAGE_ATTACHMENT_MAX_COUNT} files per message"
        )
    prepared = [_prepare_attachment_upload(name, mime, content) for name, mime, content in files]
    total_bytes = sum(item.size_bytes for item in prepared)
    if total_bytes > MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES:
        raise ValueError(
            "combined attachments exceed the "
            f"{MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES} byte total limit"
        )
    _require_secure_attachment_storage()

    if os.name == "nt":
        return _upload_attachments_windows(workspace, sid, prepared)

    session_fd = _open_attachment_session_root(workspace, sid, create=True)
    written_ids: list[str] = []
    out: list[dict[str, Any]] = []
    try:
        for item in prepared:
            written_ids.append(item.attachment_id)
            out.append(_store_prepared_attachment(session_fd, sid, item))
    except Exception as exc:
        cleanup_errors: list[str] = []
        for attachment_id in reversed(written_ids):
            try:
                _remove_tree_nofollow(
                    session_fd,
                    attachment_id,
                    display_path=_attachment_dir_relative_path(sid, attachment_id),
                )
            except OSError as cleanup_exc:
                cleanup_errors.append(f"{attachment_id}: {cleanup_exc}")
        if cleanup_errors:
            raise RuntimeError(
                f"{exc}; cleanup failed for {', '.join(cleanup_errors)}"
            ) from exc
        raise
    finally:
        os.close(session_fd)
    return {"attachments": out, "limits": attachment_limits()}


def resolve_attachment_refs(
    sid: str,
    refs: Sequence[Mapping[str, Any]],
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    if not refs:
        return []
    if len(refs) > MESSAGE_ATTACHMENT_MAX_COUNT:
        raise ValueError(
            f"too many attachments; limit is {MESSAGE_ATTACHMENT_MAX_COUNT} files per message"
        )
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        raise FileNotFoundError(f"unknown project workdir for session {sid}")
    _require_secure_attachment_storage()

    if os.name == "nt":
        return _resolve_attachment_refs_windows(workspace, sid, refs)

    seen: set[str] = set()
    attachments: list[dict[str, Any]] = []
    total_bytes = 0
    session_fd: int | None = None
    try:
        for ref in refs:
            attachment_id = str(ref.get("attachment_id") or "").strip()
            if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
                raise ValueError(f"invalid attachment_id: {attachment_id!r}")
            if attachment_id in seen:
                continue
            seen.add(attachment_id)
            if session_fd is None:
                try:
                    session_fd = _open_attachment_session_root(workspace, sid, create=False)
                except FileNotFoundError as exc:
                    raise ValueError(
                        f"unknown attachment_id for this session: {attachment_id}"
                    ) from exc
            metadata = _load_attachment_metadata(session_fd, sid, attachment_id)
            total_bytes += int(metadata["size_bytes"])
            attachments.append(metadata)
    finally:
        if session_fd is not None:
            os.close(session_fd)
    if total_bytes > MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES:
        raise ValueError(
            "combined attachments exceed the "
            f"{MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES} byte total limit"
        )
    return attachments


def attachment_context_refs(
    attachments: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for row in attachments:
        relative_path = str(row.get("relative_path") or "").strip()
        if not relative_path:
            continue
        refs.append(
            {
                "kind": "attachment",
                "ref": relative_path,
                "why": "operator-uploaded attachment in the canonical project workdir",
                "attachment_id": str(row.get("attachment_id") or "").strip(),
                "original_name": str(row.get("original_name") or "").strip(),
                "mime": str(row.get("mime") or "").strip(),
                "size_bytes": str(row.get("size_bytes") or "").strip(),
            }
        )
    return refs


def attachment_context_block(
    attachments: Sequence[Mapping[str, Any]],
) -> str:
    if not attachments:
        return ""
    lines = [
        "## Operator attachments",
        "The operator uploaded these session-scoped files into the canonical project workdir before this message.",
    ]
    for row in attachments:
        relative_path = str(row.get("relative_path") or "").strip()
        if not relative_path:
            continue
        lines.extend(
            [
                f"- attachment_id: {str(row.get('attachment_id') or '').strip()}",
                f"  path: {relative_path}",
                f"  original_name: {str(row.get('original_name') or '').strip()}",
                f"  mime: {str(row.get('mime') or '').strip()}",
                f"  size_bytes: {str(row.get('size_bytes') or '').strip()}",
            ]
        )
    return "\n".join(lines)


def compose_message_body(
    text: str,
    attachments: Sequence[Mapping[str, Any]],
) -> str:
    body = str(text or "").strip()
    context = attachment_context_block(attachments)
    if body and context:
        return body + "\n\n" + context
    return body or context


def _prepare_attachment_upload(
    raw_name: str,
    declared_mime: str,
    content: bytes,
) -> _PreparedAttachment:
    original_name = _normalize_client_filename(raw_name)
    suffix = Path(original_name).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported attachment type for {original_name}; supported suffixes are "
            "PNG, JPEG, WebP, PDF, Markdown/text, JSON, and CSV"
        )
    size_bytes = len(content)
    if size_bytes > MESSAGE_ATTACHMENT_MAX_BYTES:
        raise ValueError(
            f"{original_name} exceeds the {MESSAGE_ATTACHMENT_MAX_BYTES} byte per-file limit"
        )
    canonical_mime = _validate_attachment_payload(
        suffix=suffix,
        declared_mime=declared_mime,
        content=content,
        original_name=original_name,
    )
    attachment_id = f"att-{uuid4().hex[:12]}"
    return _PreparedAttachment(
        attachment_id=attachment_id,
        original_name=original_name,
        stored_name=_sanitize_storage_name(original_name),
        suffix=suffix,
        mime=canonical_mime,
        size_bytes=size_bytes,
        content=content,
    )


def _normalize_client_filename(raw_name: str) -> str:
    value = _CONTROL_CHAR_RE.sub("", str(raw_name or "")).strip()
    if not value:
        raise ValueError("attachment filename is empty")
    if "/" in value or "\\" in value:
        raise ValueError("attachment filename must not contain path separators")
    value = re.sub(r"\s+", " ", value).strip()
    if value in {".", ".."}:
        raise ValueError("attachment filename is invalid")
    trimmed = value[:180]
    if not Path(trimmed).suffix:
        raise ValueError("attachment filename must include a supported suffix")
    return trimmed


def _sanitize_storage_name(original_name: str) -> str:
    path = Path(original_name)
    stem = _CLIENT_FILENAME_RE.sub("_", path.stem).strip(" ._")
    if not stem:
        stem = "attachment"
    suffix = path.suffix.lower()
    budget = max(1, 120 - len(suffix))
    return stem[:budget] + suffix


def _validate_attachment_payload(
    *,
    suffix: str,
    declared_mime: str,
    content: bytes,
    original_name: str,
) -> str:
    declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if suffix in _BINARY_UPLOAD_SPECS:
        canonical_mime, allowed_declared = _BINARY_UPLOAD_SPECS[suffix]
        if declared and declared not in allowed_declared:
            raise ValueError(
                f"{original_name} declared unsupported MIME {declared}; expected {canonical_mime}"
            )
        if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{original_name} is not a valid PNG file")
        if suffix in {".jpg", ".jpeg"} and not (
            len(content) >= 3 and content[:3] == b"\xff\xd8\xff"
        ):
            raise ValueError(f"{original_name} is not a valid JPEG file")
        if suffix == ".webp" and not (
            len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        ):
            raise ValueError(f"{original_name} is not a valid WebP file")
        if suffix == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError(f"{original_name} is not a valid PDF file")
        return canonical_mime

    canonical_mime, allowed_declared = _TEXT_UPLOAD_SPECS[suffix]
    if declared and declared not in allowed_declared:
        raise ValueError(
            f"{original_name} declared unsupported MIME {declared}; expected {canonical_mime}"
        )
    text = _decode_text_attachment(content, original_name)
    if suffix == ".json":
        try:
            json.loads(text)
        except ValueError as exc:
            raise ValueError(f"{original_name} is not valid JSON") from exc
    return canonical_mime


def _decode_text_attachment(content: bytes, original_name: str) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{original_name} must be UTF-8 text") from exc
    if any(ord(char) < 32 and char not in "\n\r\t\f\b" for char in text):
        raise ValueError(f"{original_name} contains unsupported binary control bytes")
    return text


def _require_secure_attachment_storage() -> None:
    if os.name == "nt":
        return
    if os.name != "posix":
        raise RuntimeError(
            "secure attachment storage requires POSIX dir_fd and O_NOFOLLOW support"
        )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeError(
            "secure attachment storage requires O_NOFOLLOW and O_DIRECTORY support"
        )


if os.name == "nt":  # pragma: no cover - definitions are exercised on Windows
    import ctypes
    from ctypes import wintypes

    _WIN_FILE_READ_ATTRIBUTES = 0x0080
    _WIN_GENERIC_READ = 0x80000000
    _WIN_FILE_SHARE_READ = 0x00000001
    _WIN_FILE_SHARE_WRITE = 0x00000002
    _WIN_OPEN_EXISTING = 3
    _WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WIN_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _WinByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _WIN_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WIN_CREATE_FILE = _WIN_KERNEL32.CreateFileW
    _WIN_CREATE_FILE.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _WIN_CREATE_FILE.restype = wintypes.HANDLE
    _WIN_GET_FILE_INFORMATION = _WIN_KERNEL32.GetFileInformationByHandle
    _WIN_GET_FILE_INFORMATION.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WinByHandleFileInformation),
    ]
    _WIN_GET_FILE_INFORMATION.restype = wintypes.BOOL
    _WIN_CLOSE_HANDLE = _WIN_KERNEL32.CloseHandle
    _WIN_CLOSE_HANDLE.argtypes = [wintypes.HANDLE]
    _WIN_CLOSE_HANDLE.restype = wintypes.BOOL


@contextmanager
def _windows_guard_path(path: Path, *, directory: bool) -> Iterator[None]:
    """Hold a Windows path open and reject symlinks/junctions atomically.

    Omitting ``FILE_SHARE_DELETE`` prevents the verified object from being
    renamed or replaced while its guard is held. ``OPEN_REPARSE_POINT`` makes
    the handle refer to the link/junction itself so the attribute check cannot
    be raced into silently opening its target.
    """
    if os.name != "nt":  # pragma: no cover - Windows-only helper
        yield
        return
    flags = _WIN_FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _WIN_FILE_FLAG_BACKUP_SEMANTICS
    handle = _WIN_CREATE_FILE(
        str(path),
        _WIN_FILE_READ_ATTRIBUTES,
        _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE,
        None,
        _WIN_OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _WIN_INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), f"cannot securely open {path}")
    try:
        info = _WinByHandleFileInformation()
        if not _WIN_GET_FILE_INFORMATION(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), f"cannot inspect {path}")
        attributes = int(info.dwFileAttributes)
        if attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"attachment storage must not traverse reparse points: {path}")
        is_directory = bool(attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY)
        if directory and not is_directory:
            raise ValueError(f"attachment storage path is not a directory: {path}")
        if not directory and is_directory:
            raise ValueError(f"attachment path is not a regular file: {path}")
        yield
    finally:
        _WIN_CLOSE_HANDLE(handle)


def _windows_guard_directory_chain(path: Path, stack: ExitStack) -> Path:
    """Guard every existing directory component from the drive root down."""
    absolute = path.expanduser().absolute()
    anchor = Path(absolute.anchor)
    current = anchor
    for part in absolute.parts[1:]:
        current = current / part
        stack.enter_context(_windows_guard_path(current, directory=True))
    return absolute


def _windows_open_attachment_session_root(
    workspace: Path,
    sid: str,
    *,
    create: bool,
    stack: ExitStack,
) -> Path:
    _validate_storage_component(sid)
    current = _windows_guard_directory_chain(workspace, stack)
    for part in (*ATTACHMENT_ROOT.parts, sid):
        _validate_storage_component(part)
        child = current / part
        if create:
            try:
                child.mkdir()
            except FileExistsError:
                pass
        stack.enter_context(_windows_guard_path(child, directory=True))
        current = child
    return current


def _windows_write_file_atomic(parent: Path, name: str, content: bytes) -> None:
    _validate_storage_component(name)
    temporary = parent / f".{name}.tmp-{os.getpid()}-{time.time_ns()}-{uuid4().hex}"
    target = parent / name
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOINHERIT", 0),
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def _windows_store_prepared_attachment(
    session_root: Path,
    sid: str,
    item: _PreparedAttachment,
) -> dict[str, Any]:
    attachment_root = session_root / item.attachment_id
    attachment_root.mkdir()
    try:
        with _windows_guard_path(attachment_root, directory=True):
            relative_path = _attachment_payload_relative_path(
                sid, item.attachment_id, item.stored_name
            )
            _windows_write_file_atomic(attachment_root, item.stored_name, item.content)
            payload = {
                "schema_version": ATTACHMENT_SCHEMA_VERSION,
                "session_id": sid,
                "attachment_id": item.attachment_id,
                "relative_path": relative_path,
                "original_name": item.original_name,
                "stored_name": item.stored_name,
                "mime": item.mime,
                "size_bytes": item.size_bytes,
                "created_at": time.time(),
            }
            _windows_write_file_atomic(
                attachment_root,
                "metadata.json",
                (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                ),
            )
            return payload
    except Exception:
        _windows_remove_tree_nofollow(attachment_root)
        raise


def _windows_remove_tree_nofollow(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    attributes = int(getattr(info, "st_file_attributes", 0))
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        path.unlink()
        return
    if not stat.S_ISDIR(info.st_mode):
        path.unlink()
        return
    for child in path.iterdir():
        _windows_remove_tree_nofollow(child)
    path.rmdir()


def _upload_attachments_windows(
    workspace: Path,
    sid: str,
    prepared: Sequence[_PreparedAttachment],
) -> dict[str, Any]:
    written: list[Path] = []
    out: list[dict[str, Any]] = []
    with ExitStack() as stack:
        session_root = _windows_open_attachment_session_root(
            workspace, sid, create=True, stack=stack
        )
        try:
            for item in prepared:
                out.append(_windows_store_prepared_attachment(session_root, sid, item))
                written.append(session_root / item.attachment_id)
        except Exception:
            for path in reversed(written):
                _windows_remove_tree_nofollow(path)
            raise
    return {"attachments": out, "limits": attachment_limits()}


def _windows_read_regular_file(
    path: Path,
    *,
    display_path: str,
    max_bytes: int,
) -> bytes:
    try:
        with _windows_guard_path(path, directory=False):
            with path.open("rb") as handle:
                content = handle.read(max_bytes + 1)
    except FileNotFoundError as exc:
        raise FileNotFoundError(display_path) from exc
    if len(content) > max_bytes:
        raise ValueError(f"attachment file is too large to read safely: {display_path}")
    return content


def _windows_load_attachment_metadata(
    session_root: Path,
    sid: str,
    attachment_id: str,
) -> dict[str, Any]:
    attachment_root = session_root / attachment_id
    try:
        with _windows_guard_path(attachment_root, directory=True):
            raw = _windows_read_regular_file(
                attachment_root / "metadata.json",
                display_path=f"{attachment_id}/metadata.json",
                max_bytes=_ATTACHMENT_METADATA_MAX_BYTES,
            )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                raise ValueError(f"attachment metadata is malformed for {attachment_id}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"attachment metadata is malformed for {attachment_id}")
            stored_name = _validate_metadata_payload(payload, sid, attachment_id)
            content = _windows_read_regular_file(
                attachment_root / stored_name,
                display_path=_attachment_payload_relative_path(sid, attachment_id, stored_name),
                max_bytes=MESSAGE_ATTACHMENT_MAX_BYTES,
            )
    except FileNotFoundError as exc:
        raise ValueError(f"unknown attachment_id for this session: {attachment_id}") from exc
    try:
        expected_size = int(payload.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"attachment metadata is malformed for {attachment_id}") from exc
    if len(content) != expected_size:
        raise ValueError(f"attachment payload size mismatch for {attachment_id}")
    payload.pop("sha256", None)
    payload.pop("integrity", None)
    return payload


def _resolve_attachment_refs_windows(
    workspace: Path,
    sid: str,
    refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    attachments: list[dict[str, Any]] = []
    total_bytes = 0
    with ExitStack() as stack:
        try:
            session_root = _windows_open_attachment_session_root(
                workspace, sid, create=False, stack=stack
            )
        except OSError as exc:
            first = str(refs[0].get("attachment_id") or "").strip()
            raise ValueError(f"unknown attachment_id for this session: {first}") from exc
        for ref in refs:
            attachment_id = str(ref.get("attachment_id") or "").strip()
            if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
                raise ValueError(f"invalid attachment_id: {attachment_id!r}")
            if attachment_id in seen:
                continue
            seen.add(attachment_id)
            metadata = _windows_load_attachment_metadata(
                session_root, sid, attachment_id
            )
            total_bytes += int(metadata["size_bytes"])
            attachments.append(metadata)
    if total_bytes > MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES:
        raise ValueError(
            "combined attachments exceed the "
            f"{MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES} byte total limit"
        )
    return attachments


def _attachment_dir_relative_path(sid: str, attachment_id: str) -> str:
    return PurePosixPath(*ATTACHMENT_ROOT.parts, sid, attachment_id).as_posix()


def _attachment_payload_relative_path(sid: str, attachment_id: str, stored_name: str) -> str:
    return PurePosixPath(*ATTACHMENT_ROOT.parts, sid, attachment_id, stored_name).as_posix()


def _open_attachment_session_root(workspace: Path, sid: str, *, create: bool) -> int:
    current_fd = _open_workspace_directory(workspace)
    display_parts: list[str] = []
    try:
        for part in (*ATTACHMENT_ROOT.parts, sid):
            display_parts.append(part)
            next_fd = _open_storage_directory(
                current_fd,
                part,
                display_path=PurePosixPath(*display_parts).as_posix(),
                create=create,
                must_create=False,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_workspace_directory(workspace: Path) -> int:
    try:
        descriptor = os.open(os.fspath(workspace.expanduser()), _DIRECTORY_OPEN_FLAGS)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"workspace not found: {workspace}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"workspace path must not be a symlink: {workspace}") from exc
        if exc.errno == errno.ENOTDIR:
            raise ValueError(f"workspace path is not a directory: {workspace}") from exc
        raise
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISDIR(mode):
            raise ValueError(f"workspace path is not a directory: {workspace}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_storage_directory(
    parent_fd: int,
    name: str,
    *,
    display_path: str,
    create: bool,
    must_create: bool,
) -> int:
    _validate_storage_component(name)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            if must_create:
                raise FileExistsError(f"attachment storage already exists for {name}") from exc
        except OSError as exc:
            raise _directory_error(parent_fd, name, display_path, exc) from exc
    return _open_verified_directory(parent_fd, name, display_path=display_path)


def _open_verified_directory(parent_fd: int, name: str, *, display_path: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise _directory_error(parent_fd, name, display_path, exc) from exc
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISDIR(mode):
            raise ValueError(f"attachment storage path is not a directory: {display_path}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _directory_error(parent_fd: int, name: str, display_path: str, exc: OSError) -> Exception:
    if isinstance(exc, FileNotFoundError):
        return FileNotFoundError(display_path)
    mode = _lstat_mode(parent_fd, name)
    if (exc.errno == errno.ELOOP) or (mode is not None and stat.S_ISLNK(mode)):
        return ValueError(f"attachment storage must not traverse symlinks: {display_path}")
    if exc.errno == errno.ENOTDIR:
        return ValueError(f"attachment storage path is not a directory: {display_path}")
    return OSError(f"cannot access attachment storage path {display_path}: {exc}")


def _lstat_mode(parent_fd: int, name: str) -> int | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
    except OSError:
        return None


def _validate_storage_component(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid attachment storage component: {name!r}")


def _store_prepared_attachment(
    session_fd: int,
    sid: str,
    item: _PreparedAttachment,
) -> dict[str, Any]:
    attachment_fd = _open_storage_directory(
        session_fd,
        item.attachment_id,
        display_path=_attachment_dir_relative_path(sid, item.attachment_id),
        create=True,
        must_create=True,
    )
    try:
        relative_path = _attachment_payload_relative_path(
            sid, item.attachment_id, item.stored_name
        )
        _write_file_atomic(attachment_fd, item.stored_name, item.content)
        payload = {
            "schema_version": ATTACHMENT_SCHEMA_VERSION,
            "session_id": sid,
            "attachment_id": item.attachment_id,
            "relative_path": relative_path,
            "original_name": item.original_name,
            "stored_name": item.stored_name,
            "mime": item.mime,
            "size_bytes": item.size_bytes,
            "created_at": time.time(),
        }
        _write_file_atomic(
            attachment_fd,
            "metadata.json",
            (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        return payload
    finally:
        os.close(attachment_fd)


def _write_file_atomic(parent_fd: int, name: str, content: bytes) -> None:
    _validate_storage_component(name)
    temporary = f".{name}.tmp-{os.getpid()}-{time.time_ns()}-{uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, _FILE_WRITE_FLAGS, 0o600, dir_fd=parent_fd)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _fsync_directory(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent_fd)


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        return


def _load_attachment_metadata(session_fd: int, sid: str, attachment_id: str) -> dict[str, Any]:
    try:
        attachment_fd = _open_verified_directory(
            session_fd,
            attachment_id,
            display_path=_attachment_dir_relative_path(sid, attachment_id),
        )
    except FileNotFoundError as exc:
        raise ValueError(f"unknown attachment_id for this session: {attachment_id}") from exc
    try:
        payload = _read_attachment_metadata_payload(attachment_fd, attachment_id)
        stored_name = _validate_metadata_payload(payload, sid, attachment_id)
        try:
            expected_size = int(payload.get("size_bytes") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"attachment metadata is malformed for {attachment_id}") from exc
        actual_size = _measure_regular_file(
            attachment_fd,
            stored_name,
            display_path=_attachment_payload_relative_path(sid, attachment_id, stored_name),
        )
    except OSError as exc:
        raise ValueError(f"attachment payload is unavailable for {attachment_id}") from exc
    finally:
        os.close(attachment_fd)

    if actual_size != expected_size:
        raise ValueError(f"attachment payload size mismatch for {attachment_id}")
    payload.pop("sha256", None)
    payload.pop("integrity", None)
    return payload


def _read_attachment_metadata_payload(attachment_fd: int, attachment_id: str) -> dict[str, Any]:
    try:
        raw = _read_regular_file_bytes(
            attachment_fd,
            "metadata.json",
            display_path=f"{attachment_id}/metadata.json",
            max_bytes=_ATTACHMENT_METADATA_MAX_BYTES,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"unknown attachment_id for this session: {attachment_id}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read attachment metadata for {attachment_id}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError(f"attachment metadata is malformed for {attachment_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"attachment metadata is malformed for {attachment_id}")
    return payload


def _validate_metadata_payload(payload: dict[str, Any], sid: str, attachment_id: str) -> str:
    if str(payload.get("session_id") or "") != sid:
        raise ValueError(f"attachment_id {attachment_id} does not belong to session {sid}")
    if str(payload.get("attachment_id") or "") != attachment_id:
        raise ValueError(f"attachment metadata id mismatch for {attachment_id}")
    stored_name = _validate_stored_name(str(payload.get("stored_name") or ""), attachment_id)
    relative_path = str(payload.get("relative_path") or "").strip().replace("\\", "/")
    expected_relative = _attachment_payload_relative_path(sid, attachment_id, stored_name)
    if relative_path != expected_relative:
        raise ValueError(f"attachment metadata relative_path mismatch for {attachment_id}")
    return stored_name


def _validate_stored_name(value: str, attachment_id: str) -> str:
    stored_name = _CONTROL_CHAR_RE.sub("", str(value or "")).strip()
    if not stored_name or stored_name in {".", ".."} or "/" in stored_name or "\\" in stored_name:
        raise ValueError(f"attachment metadata stored_name is invalid for {attachment_id}")
    suffix = Path(stored_name).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"attachment metadata stored_name is invalid for {attachment_id}")
    return stored_name


def _read_regular_file_bytes(
    parent_fd: int,
    name: str,
    *,
    display_path: str,
    max_bytes: int | None = None,
) -> bytes:
    descriptor = _open_regular_file(parent_fd, name, display_path=display_path)
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, _ATTACHMENT_IO_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"attachment file is too large to read safely: {display_path}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _measure_regular_file(parent_fd: int, name: str, *, display_path: str) -> int:
    descriptor = _open_regular_file(parent_fd, name, display_path=display_path)
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, _ATTACHMENT_IO_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MESSAGE_ATTACHMENT_MAX_BYTES:
                raise ValueError(f"attachment payload size mismatch for {Path(display_path).parent.name}")
        return total
    finally:
        os.close(descriptor)


def _open_regular_file(parent_fd: int, name: str, *, display_path: str) -> int:
    _validate_storage_component(name)
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise FileNotFoundError(display_path) from exc
        if exc.errno == errno.ELOOP:
            raise ValueError(f"attachment path must not traverse symlinks: {display_path}") from exc
        raise
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"attachment path is not a regular file: {display_path}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _remove_tree_nofollow(parent_fd: int, name: str, *, display_path: str) -> None:
    try:
        mode = os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OSError(f"cannot inspect attachment cleanup path {display_path}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        os.unlink(name, dir_fd=parent_fd)
        return

    try:
        child_fd = _open_verified_directory(parent_fd, name, display_path=display_path)
    except FileNotFoundError:
        return
    except ValueError:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return
        return
    try:
        for entry in os.scandir(child_fd):
            _remove_tree_nofollow(
                child_fd,
                entry.name,
                display_path=f"{display_path}/{entry.name}",
            )
    finally:
        os.close(child_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=parent_fd)
            return
        raise


__all__ = [
    "ATTACHMENT_ROOT",
    "MESSAGE_ATTACHMENT_MAX_BYTES",
    "MESSAGE_ATTACHMENT_MAX_COUNT",
    "MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES",
    "attachment_context_block",
    "attachment_context_refs",
    "attachment_limits",
    "compose_message_body",
    "resolve_attachment_refs",
    "upload_attachments",
]
