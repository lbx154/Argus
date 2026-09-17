"""The knowledge journal: one line each time a page is learned, recalled or shared.

``<global_root>/knowledge-journal.jsonl`` is the host-wide record of how the
knowledge library is used. A ``learned`` line is written when a role writes a
new page, a ``recalled`` line when a page is placed in front of a role, and a
``promoted`` line when a project page is copied into a shared tier. The Web UI
reads it as a live feed and counts the ``recalled`` lines per page so a reader
can see which lessons keep paying off.

Every record has the same shape::

    {"ts": float, "kind": "learned"|"recalled"|"promoted",
     "scope": "project"|"vertical"|"global", "vertical": str,
     "path": str, "title": str, "source_project": str, "mission_id": str,
     "role": str, "page_kind": str, "note": str}

``path`` is relative to the library root that owns the page
(``pages/lessons/20260917-torch-search.md`` or ``principles.md``).

Writing is an append of one line under an advisory lock and never raises to the
caller: a mission or a reply must not fail because the journal could not be
written. Reading walks the file from its end so the newest records cost the
same however long the journal has grown.

Layer: capabilities
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ..core.file_lock import FileLockCancelled, exclusive_file_lock
from ..core.json_codec import loads_finite_json
from ..core.secret_guard import redact_secrets_text

log = logging.getLogger(__name__)

JOURNAL_FILENAME = "knowledge-journal.jsonl"
JOURNAL_LOCK_FILENAME = ".knowledge-journal.lock"
KINDS: tuple[str, ...] = ("learned", "recalled", "promoted")
SCOPES: tuple[str, ...] = ("project", "vertical", "global")
RECORD_FIELDS: tuple[str, ...] = (
    "kind", "scope", "vertical", "path", "title",
    "source_project", "mission_id", "role", "page_kind", "note",
)
_TEXT_LIMIT = 2_000
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.02
_TAIL_CHUNK_BYTES = 64 * 1024
_MAX_RECORD_BYTES = 64 * 1024
# A tail read stops after this many bytes even when fewer than ``limit``
# records matched, so a filter that matches nothing cannot scan a huge file.
_MAX_TAIL_SCAN_BYTES = 16 * 1024 * 1024


def journal_path(global_root: str | Path) -> Path:
    return Path(global_root).expanduser() / JOURNAL_FILENAME


def _text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit]
    return redact_secrets_text(text)


def _record(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The normalized journal record, or None when the fields cannot form one."""
    kind = _text(fields.get("kind")).lower()
    if kind not in KINDS:
        log.warning("knowledge journal: dropped record with unknown kind %r", kind)
        return None
    raw_ts = fields.get("ts")
    ts = time.time()
    if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool) and math.isfinite(raw_ts):
        ts = float(raw_ts)
    record: dict[str, Any] = {"ts": ts, "kind": kind}
    for name in RECORD_FIELDS[1:]:
        record[name] = _text(fields.get(name))
    record["scope"] = record["scope"].lower()
    return record


def append_knowledge_event(global_root: str | Path, **fields: Any) -> None:
    """Append one record. Problems are logged, never raised to the caller."""
    record = _record(fields)
    if record is None:
        return
    path = journal_path(global_root)
    try:
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(JOURNAL_LOCK_FILENAME)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            with exclusive_file_lock(
                lock_handle,
                timeout_seconds=_LOCK_TIMEOUT_SECONDS,
                poll_seconds=_LOCK_POLL_SECONDS,
                lock_name=f"knowledge journal lock {lock_path}",
            ):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    view = memoryview(line)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                finally:
                    os.close(fd)
    except (OSError, FileLockCancelled, TypeError, ValueError) as exc:
        # TimeoutError is an OSError: a held lock is reported the same way.
        log.warning("knowledge journal: could not append to %s: %s", path, exc)


def _parse_line(raw: bytes) -> dict[str, Any] | None:
    if not raw or len(raw) > _MAX_RECORD_BYTES:
        return None
    try:
        value = loads_finite_json(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        return None
    return value


def _iter_lines_backwards(path: Path, *, max_bytes: int) -> Iterator[bytes]:
    """Complete lines of ``path`` from the last to the first, reading in chunks.

    An unfinished final line (no trailing newline: a write still in flight) is
    not a line yet and is skipped. Stops after ``max_bytes`` have been read.
    """
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        scanned = 0
        remainder = b""
        trimming_tail = True
        while position > 0 and scanned < max_bytes:
            size = min(_TAIL_CHUNK_BYTES, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size)
            scanned += size
            buffer = chunk + remainder
            if trimming_tail:
                cut = buffer.rfind(b"\n")
                if cut < 0:
                    # Still inside the unfinished last line; keep looking back.
                    remainder = buffer
                    continue
                buffer = buffer[: cut + 1]
                trimming_tail = False
            lines = buffer.split(b"\n")
            # ``lines[0]`` may be the tail of a line that starts in an earlier
            # chunk; it becomes the remainder unless this is the file's start.
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line
            if position == 0 and remainder:
                yield remainder
                remainder = b""


def read_knowledge_events(
    global_root: str | Path,
    *,
    limit: int = 100,
    kinds: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """The newest ``limit`` records, newest first, optionally of some kinds only."""
    if isinstance(kinds, str):
        kinds = (kinds,)
    wanted = {str(kind).strip().lower() for kind in kinds} if kinds is not None else None
    count = max(0, int(limit))
    if count == 0:
        return []
    path = journal_path(global_root)
    rows: list[dict[str, Any]] = []
    try:
        for raw in _iter_lines_backwards(path, max_bytes=_MAX_TAIL_SCAN_BYTES):
            record = _parse_line(raw)
            if record is None:
                continue
            if wanted is not None and record.get("kind") not in wanted:
                continue
            rows.append(record)
            if len(rows) >= count:
                break
    except OSError:
        return rows
    return rows


def iter_knowledge_events(global_root: str | Path) -> Iterator[dict[str, Any]]:
    """Every readable record, oldest first, streamed one line at a time."""
    path = journal_path(global_root)
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                record = _parse_line(raw.rstrip(b"\r\n"))
                if record is not None:
                    yield record
    except OSError:
        return


def reuse_counts(global_root: str | Path) -> dict[tuple[str, str, str], int]:
    """How many times each page was recalled, keyed by ``(scope, vertical, path)``."""
    counts: dict[tuple[str, str, str], int] = {}
    for record in iter_knowledge_events(global_root):
        if record.get("kind") != "recalled":
            continue
        key = (
            str(record.get("scope") or ""),
            str(record.get("vertical") or ""),
            str(record.get("path") or ""),
        )
        if not key[2]:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = [
    "JOURNAL_FILENAME",
    "KINDS",
    "RECORD_FIELDS",
    "SCOPES",
    "append_knowledge_event",
    "iter_knowledge_events",
    "journal_path",
    "read_knowledge_events",
    "reuse_counts",
]
