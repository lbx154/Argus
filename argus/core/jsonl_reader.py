"""Bounded JSONL reads for event transport and incremental history indexes.

Positions are complete-line boundaries, except while discarding an oversized
record. That one boolean makes long records resumable without persisting or
buffering their contents. A batch reads at most its byte budget plus one record
limit; all JSON decoding applies the HTTP finite-number boundary.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from .file_lock import exclusive_file_lock
from .json_codec import loads_finite_json

MAX_JSONL_RECORD_BYTES = 1024 * 1024
TAIL_BATCH_BYTES = 256 * 1024
REPLAY_BYTES = 256 * 1024
REPLAY_GENERATIONS = 64


@dataclass(frozen=True, slots=True)
class JsonlBatch:
    rows: list[dict[str, Any]]
    offset: int
    discarding: bool = False
    bytes_read: int = 0
    more: bool = False
    waiting_for_line: bool = False
    skipped_rows: int = 0
    oversized_rows: int = 0


def _record(raw: bytes) -> dict[str, Any] | None:
    try:
        value = loads_finite_json(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl_batch(
    stream: BinaryIO, offset: int, *, byte_limit: int,
    discarding: bool = False, sealed: bool = False,
    max_record_bytes: int | None = None,
) -> JsonlBatch:
    """Read one bounded batch from an already-open, identity-stable file.

    An ordinary unfinished line remains at its start for a later append. Once
    the record limit is exceeded, advance in bounded pieces until its newline;
    a sealed generation can discard an unfinished final record permanently.
    """
    limit = max(1, int(byte_limit))
    record_limit = max(1, int(MAX_JSONL_RECORD_BYTES if max_record_bytes is None else max_record_bytes))
    end = os.fstat(stream.fileno()).st_size
    stream.seek(offset)
    rows: list[dict[str, Any]] = []
    read = skipped = oversized = 0
    waiting = False
    while read < limit and offset < end:
        amount = min(end - offset, limit - read if discarding else record_limit + 1)
        raw = stream.readline(amount)
        if not raw:
            break
        read += len(raw)
        if discarding:
            offset += len(raw)
            discarding = not raw.endswith(b"\n")
            continue
        if len(raw) > record_limit:
            offset += len(raw)
            discarding = not raw.endswith(b"\n")
            skipped += 1
            oversized += 1
            continue
        if not raw.endswith(b"\n"):
            if sealed:
                offset += len(raw)
                skipped += 1
            else:
                waiting = True
            break
        offset += len(raw)
        if not raw.strip():
            continue
        row = _record(raw)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    if discarding and offset >= end:
        waiting = not sealed
        if sealed:
            discarding = False
    return JsonlBatch(rows, offset, discarding, read, offset < end and not waiting,
                      waiting, skipped, oversized)


def read_jsonl_replay(
    stream: BinaryIO, *, limit: int, max_bytes: int = REPLAY_BYTES,
    preserve_partial: bool = True,
) -> JsonlBatch:
    """Read a bounded recent window and its exact live-tail starting position."""
    size = os.fstat(stream.fileno()).st_size
    start = max(0, size - max(1, int(max_bytes)))
    stream.seek(start)
    raw = stream.read(size - start)
    read = len(raw)
    last = raw.rfind(b"\n")
    if last < 0:
        offset, discarding = (0, False) if not start else (size, True)
        if start and preserve_partial:
            # A partial record can straddle the replay window but still fit the
            # record limit. Recover its start without an unbounded reverse scan.
            probe_start = max(0, size - MAX_JSONL_RECORD_BYTES)
            stream.seek(probe_start)
            probe = stream.read(size - probe_start)
            read += len(probe)
            newline = probe.rfind(b"\n")
            if newline >= 0:
                offset, discarding = probe_start + newline + 1, False
            elif not probe_start:
                offset, discarding = 0, False
        return JsonlBatch([], offset, discarding, read, waiting_for_line=bool(size))
    offset = start + last + 1
    complete = raw[:last + 1]
    if start:
        _, _, complete = complete.partition(b"\n")
    rows = [row for line in complete.splitlines() if line.strip() and (row := _record(line)) is not None] if limit > 0 else []
    return JsonlBatch(rows[-max(0, int(limit)):] if limit > 0 else [], offset,
                      bytes_read=read, waiting_for_line=offset < size)


@contextmanager
def event_log_read_lock(path: Path) -> Iterator[None]:
    """Match the canonical writer's lock only during identity/read operations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "events.lock").open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="event log read"):
            yield


def retained_jsonl_paths(path: Path) -> list[Path]:
    from ..life.event_log import event_log_paths

    return event_log_paths(path)


def open_jsonl_generation(path: Path) -> BinaryIO:
    """Keep a read-only log generation open without preventing Windows rotation.

    This sharing policy is only for trusted append-only transport logs, not
    artifact/attachment security handles that deliberately prohibit replacement.
    """
    # Static checkers also need the platform boundary for Win32-only APIs.
    if sys.platform != "win32":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; OPEN_EXISTING.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


class JsonlTail:
    """One open generation, drained before following its retained successors."""

    def __init__(self, path: Path):
        self.path = path
        self.stream: BinaryIO | None = None
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.discarding = False
        self._observed: tuple[int, int] | None = None

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def _open(self, path: Path) -> None:
        stream = open_jsonl_generation(path)
        try:
            stat = os.fstat(stream.fileno())
        except BaseException:
            stream.close()
            raise
        self.close()
        self.stream = stream
        self.identity = (stat.st_dev, stat.st_ino)
        self.offset, self.discarding, self._observed = 0, False, None

    def start(
        self, *, replay_limit: int,
        merge_rows: Callable[..., list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        with event_log_read_lock(self.path):
            paths = retained_jsonl_paths(self.path)
            if not paths:
                return []
            self._open(paths[-1])
            assert self.stream is not None
            replay = read_jsonl_replay(self.stream, limit=replay_limit)
            self.offset, self.discarding = replay.offset, replay.discarding
            rows = replay.rows
            # Byte and generation budgets also bound empty/invalid archives.
            remaining = REPLAY_BYTES
            for previous in reversed(paths[:-1][-REPLAY_GENERATIONS:]):
                if replay_limit <= len(rows) or remaining <= 0:
                    break
                with open_jsonl_generation(previous) as stream:
                    older = read_jsonl_replay(stream, limit=replay_limit,
                                             max_bytes=remaining, preserve_partial=False)
                remaining -= older.bytes_read
                rows = merge_rows(older.rows, rows, limit=replay_limit)
            return rows

    def read_batch(self, *, byte_limit: int = TAIL_BATCH_BYTES) -> JsonlBatch:
        with event_log_read_lock(self.path):
            try:
                current = self.path.stat()
            except FileNotFoundError:
                current = None
            if self.stream is None:
                if current is None:
                    return JsonlBatch([], self.offset)
                # Everything appeared after an initially empty subscription;
                # rapid rotations before its first poll are still new events.
                appeared = retained_jsonl_paths(self.path)
                self._open(appeared[0] if appeared else self.path)
            assert self.stream is not None
            held = os.fstat(self.stream.fileno())
            sealed = current is None or (current.st_dev, current.st_ino) != self.identity
            if held.st_size < self.offset:
                # Invalidate BufferedReader's old contents after an in-place
                # truncation before seeking back to the new file's start.
                self.stream.seek(0, os.SEEK_END)
                self.offset, self.discarding, self._observed = 0, False, None
            observed = (held.st_size, held.st_mtime_ns)
            if not sealed and self._observed == observed:
                return JsonlBatch([], self.offset, self.discarding)
            batch = read_jsonl_batch(self.stream, self.offset, byte_limit=byte_limit,
                                     discarding=self.discarding, sealed=sealed)
            self.offset, self.discarding = batch.offset, batch.discarding
            # Cache only an exhausted/partial tail, never unread complete rows.
            self._observed = observed if not batch.more else None
            if sealed and self.offset >= held.st_size:
                paths = retained_jsonl_paths(self.path)
                position = next((i for i, path in enumerate(paths)
                                 if (path.stat().st_dev, path.stat().st_ino) == self.identity), None)
                following = paths[position + 1:] if position is not None else ([self.path] if current else [])
                if following:
                    self._open(following[0])
                    return JsonlBatch(batch.rows, batch.offset, bytes_read=batch.bytes_read,
                                      more=True, skipped_rows=batch.skipped_rows,
                                      oversized_rows=batch.oversized_rows)
            return batch
