"""Bounded canonical experience snapshots and streaming legacy migration.

One atomic replacement commits current records, retained history and the
admission watermark together. SQLite is a disposable projection of this file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from ..core.file_lock import DEFAULT_FILE_LOCK_TIMEOUT_SECONDS, exclusive_file_lock
from ..core.scoped_file import open_regular_file

if TYPE_CHECKING:
    from .failure_experience import FailureExperience

_SCHEMA_VERSION = 2
_MAX_RECORD_BYTES = 128 * 1024
_MAX_SOURCE_BYTES = 32 * 1024 * 1024


def _encoded(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _row(item: FailureExperience, kind: str = "experience") -> bytes:
    return _encoded({"record_type": kind, **item.to_jsonable()})


@dataclass
class ExperienceSnapshot:
    current: dict[str, FailureExperience] = field(default_factory=dict)
    history: list[FailureExperience] = field(default_factory=list)
    admission_floor: float = 0.0
    generation: int = 0
    digest: str = ""
    legacy: bool = False


def active(item: FailureExperience, *, now: float) -> bool:
    return item.state == "active" and (not item.expires_at or item.expires_at > now)


class ExperienceRepository:
    def __init__(
        self,
        path: Path,
        *,
        max_active: int = 256,
        max_history: int = 64,
        max_active_bytes: int = 1_000_000,
        max_history_bytes: int = 256_000,
    ) -> None:
        if not 1 <= max_active <= 4096 or not 0 <= max_history <= 4096:
            raise ValueError("experience record capacity is outside its supported bounds")
        if not 256 <= max_active_bytes <= 8_000_000 or not 0 <= max_history_bytes <= 8_000_000:
            raise ValueError("experience byte capacity is outside its supported bounds")
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.max_active = max_active
        self.max_history = max_history
        self.max_active_bytes = max_active_bytes
        self.max_history_bytes = max_history_bytes

    def _guard_paths(self) -> None:
        for path in (self.path, self.lock_path):
            absolute = path.absolute()
            if absolute.resolve() != absolute or path.is_symlink():
                raise ValueError("experience source and lock must not follow aliases")
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("experience source and lock must be regular files")

    @staticmethod
    def _open_file(path: Path, flags: int):
        return open_regular_file(path, flags)

    @contextmanager
    def locked(self, *, timeout_seconds: float = DEFAULT_FILE_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        self._guard_paths()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_file(self.lock_path, os.O_RDWR | os.O_CREAT) as handle:
            with exclusive_file_lock(handle, timeout_seconds=timeout_seconds, lock_name="failure experience lock"):
                self._guard_paths()
                yield

    @staticmethod
    def validate(item: FailureExperience) -> None:
        if not item.id or len(item.id) > 200 or item.revision < 1:
            raise ValueError("experience requires a bounded identity and positive revision")
        if item.state not in {"active", "stale", "superseded", "retracted", "retired"}:
            raise ValueError("unknown experience lifecycle state")
        if not all(
            math.isfinite(value) and value >= 0
            for value in (item.created_at, item.updated_at, item.expires_at)
        ):
            raise ValueError("experience timestamps must be finite and nonnegative")
        if len(_row(item)) > _MAX_RECORD_BYTES:
            raise ValueError("experience exceeds the per-record byte limit")
        if item.id.startswith("exp:"):
            parts = item.id.split(":")
            if len(parts) != 3 or not parts[2] or float.fromhex(parts[1]) != item.created_at:
                raise ValueError(
                    "experience identity does not match its original creation timestamp"
                )

    def load(self, *, full_legacy: bool = False, max_bytes: int = 1_000_000) -> ExperienceSnapshot:
        from .failure_experience import FailureExperience

        self._guard_paths()
        try:
            handle = self._open_file(self.path, os.O_RDONLY)
        except FileNotFoundError:
            return ExperienceSnapshot()
        with handle:
            first = handle.readline(_MAX_RECORD_BYTES + 1)
            try:
                header = json.loads(first)
            except (ValueError, UnicodeError):
                if b'"record_type": "snapshot"' in first or b'"admission_floor"' in first:
                    raise ValueError("experience snapshot header is corrupt") from None
                header = None
            if (
                isinstance(header, dict)
                and any(
                    key in header
                    for key in (
                        "admission_floor",
                        "records_digest",
                        "current_count",
                        "schema_version",
                    )
                )
                and header.get("record_type") != "snapshot"
            ):
                raise ValueError("experience snapshot header is corrupt")
            if not isinstance(header, dict) or header.get("record_type") != "snapshot":
                handle.seek(0)
                return self._legacy(handle, full=full_legacy, max_bytes=max_bytes)
            if header.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("unsupported experience snapshot version")
            body = handle.read(_MAX_SOURCE_BYTES + 1)
            if len(body) > _MAX_SOURCE_BYTES or hashlib.sha256(body).hexdigest() != header.get(
                "records_digest"
            ):
                raise ValueError("experience snapshot is truncated or corrupt")
            metadata = dict(header)
            signature = metadata.pop("snapshot_digest", None)
            if hashlib.sha256(_encoded(metadata) + body).hexdigest() != signature:
                raise ValueError("experience snapshot metadata is corrupt")
            snapshot = ExperienceSnapshot(
                admission_floor=float(header["admission_floor"]),
                generation=int(header["generation"]),
                digest=hashlib.sha256(first + body).hexdigest(),
            )
            if not math.isfinite(snapshot.admission_floor) or snapshot.admission_floor < 0:
                raise ValueError("invalid experience admission watermark")
            for line in body.splitlines():
                row = json.loads(line)
                item = FailureExperience.from_jsonable(row)
                self.validate(item)
                if row.get("record_type") == "history":
                    snapshot.history.append(item)
                elif row.get("record_type") == "experience" and item.id not in snapshot.current:
                    snapshot.current[item.id] = item
                else:
                    raise ValueError("invalid or duplicate experience snapshot record")
            if (
                len(snapshot.current) != header["current_count"]
                or len(snapshot.history) != header["history_count"]
            ):
                raise ValueError("experience snapshot record count mismatch")
            return snapshot

    def _legacy(self, handle: Any, *, full: bool, max_bytes: int) -> ExperienceSnapshot:
        from .failure_experience import FailureAnnotation, FailureExperience

        snapshot = ExperienceSnapshot(legacy=True)
        if not full:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - max(0, max_bytes))
            handle.seek(start)
            if start:
                handle.readline(_MAX_RECORD_BYTES + 1)
        digest = hashlib.sha256()
        while True:
            raw = handle.readline(_MAX_RECORD_BYTES + 1)
            if not raw:
                break
            if len(raw) > _MAX_RECORD_BYTES:
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(_MAX_RECORD_BYTES + 1)
                continue
            digest.update(raw)
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    continue
                if row.get("record_type") == "annotation":
                    previous = snapshot.current.get(str(row.get("experience_id") or ""))
                    payload = row.get("annotation")
                    if previous is not None and isinstance(payload, dict):
                        annotation = FailureAnnotation(
                            id=str(payload.get("id") or hashlib.sha256(raw).hexdigest()[:16]),
                            created_at=float(payload.get("created_at") or previous.created_at),
                            text=str(payload.get("text") or "")[:4000],
                            relation=str(payload.get("relation") or "")[:200],
                            evidence_refs=[
                                str(ref)[:800] for ref in (payload.get("evidence_refs") or [])[:24]
                            ],
                        )
                        snapshot.current[previous.id] = replace(
                            previous, annotations=(previous.annotations + [annotation])[-24:]
                        )
                    continue
                if row.get("record_type") not in {None, "experience"}:
                    continue
                # Missing old identities receive a deterministic migration identity.
                row.setdefault("id", hashlib.sha256(raw).hexdigest()[:16])
                item = FailureExperience.from_jsonable(row)
                self.validate(item)
                previous = snapshot.current.get(item.id)
                if previous is None and item.created_at <= snapshot.admission_floor:
                    continue
                if previous is None or item.revision >= previous.revision:
                    snapshot.current[item.id] = item
            except (TypeError, ValueError, UnicodeError, AttributeError):
                continue
            # Migration reads the original stream once, keeping bounded state.
            if len(snapshot.current) > self.max_active * 2:
                self.trim(snapshot, now=0)
        snapshot.digest = digest.hexdigest()
        return snapshot

    def trim(
        self, snapshot: ExperienceSnapshot, *, now: float, protected: set[str] | None = None
    ) -> None:
        protected = protected or set()
        for identity, item in tuple(snapshot.current.items()):
            if item.state == "active" and item.expires_at and item.expires_at <= now:
                snapshot.history.append(item)
                snapshot.current[identity] = replace(
                    item,
                    revision=item.revision + 1,
                    state="stale",
                    updated_at=now,
                    retirement_reason="declared expiry elapsed",
                )
        live = sorted(
            (item for item in snapshot.current.values() if item.state == "active"),
            key=lambda item: (
                item.id in protected,
                bool(item.evidence_refs),
                item.updated_at or item.created_at,
                item.id,
            ),
            reverse=True,
        )
        used = 0
        retained = 0
        for item in live:
            size = len(_row(item))
            if retained < self.max_active and used + size <= self.max_active_bytes:
                retained += 1
                used += size
                continue
            if item.id in protected:
                raise ValueError("new experience cannot fit the active memory budget")
            snapshot.current[item.id] = replace(
                item,
                revision=item.revision + 1,
                state="retired",
                updated_at=now,
                retirement_reason="active memory capacity",
            )
        terminal = [item for item in snapshot.current.values() if item.state != "active"]
        candidates = [(True, item) for item in terminal] + [
            (False, item) for item in snapshot.history
        ]
        candidates.sort(
            key=lambda row: (row[1].updated_at or row[1].created_at, row[0], row[1].revision),
            reverse=True,
        )
        kept_history: list[FailureExperience] = []
        kept_terminal: set[str] = set()
        used = 0
        retained = 0
        for is_current, item in candidates:
            size = len(_row(item, "experience" if is_current else "history"))
            if retained < self.max_history and used + size <= self.max_history_bytes:
                retained += 1
                used += size
                if is_current:
                    kept_terminal.add(item.id)
                else:
                    kept_history.append(item)
            elif is_current:
                # Bound tombstones without allowing an old creation to reappear
                # as an apparently new identity after physical compaction.
                snapshot.admission_floor = max(snapshot.admission_floor, item.created_at)
        for item in terminal:
            if item.id not in kept_terminal:
                del snapshot.current[item.id]
        snapshot.history = kept_history

    def save(
        self, snapshot: ExperienceSnapshot, *, now: float, protected: set[str] | None = None
    ) -> None:
        self._guard_paths()
        self.trim(snapshot, now=now, protected=protected)
        for item in [*snapshot.current.values(), *snapshot.history]:
            self.validate(item)
        current = sorted(
            snapshot.current.values(),
            key=lambda item: (item.updated_at or item.created_at, item.id),
            reverse=True,
        )
        body = b"".join(_row(item) for item in current) + b"".join(
            _row(item, "history") for item in snapshot.history
        )
        metadata = {
            "record_type": "snapshot",
            "schema_version": _SCHEMA_VERSION,
            "generation": snapshot.generation + 1,
            "admission_floor": snapshot.admission_floor,
            "current_count": len(current),
            "history_count": len(snapshot.history),
            "records_digest": hashlib.sha256(body).hexdigest(),
        }
        metadata["snapshot_digest"] = hashlib.sha256(_encoded(metadata) + body).hexdigest()
        header = _encoded(metadata)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(header + body)
                handle.flush()
                os.fsync(handle.fileno())
            self._guard_paths()
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        snapshot.generation += 1
        snapshot.legacy = False
        snapshot.digest = hashlib.sha256(header + body).hexdigest()
