"""Persistence layer for F5 project lifecycle state.

Stores ``ProjectStatus`` per-project in ``<memory_root>/lifecycle.json`` so
the supervisor's quarantine decisions survive daemon restart. This is a
sidecar file — we do NOT mutate ``BacklogItem`` schema (that's per-mission
state; lifecycle is per-project).

Layout::

    {
      "state": "running",
      "last_state_change_at": "2026-05-29T10:15:00+00:00",
      "consecutive_no_progress_ticks": 0,
      "history": [
        {
          "at": "2026-05-29T10:15:00+00:00",
          "from_state": "incubating",
          "to_state": "running",
          "reason": "first_evidence_bundle_appeared"
        }
      ]
    }

Only the fields the policy engine needs to remember across runs are
persisted; everything observable (last_evidence_at, has_draft,
spent_usd, etc.) is recomputed from the project tree each tick.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .project_lifecycle import (
    LifecycleEvent,
    ProjectState,
    ProjectStatus,
)

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

LIFECYCLE_FILENAME = "lifecycle.json"

_HISTORY_MAX = 200  # cap to keep file size bounded
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()


class LifecycleIOError(Exception):
    """Raised when the lifecycle file is malformed and we cannot recover
    a usable persisted state."""


def lifecycle_path(memory_root: Path) -> Path:
    return Path(memory_root) / LIFECYCLE_FILENAME


@contextmanager
def _lifecycle_lock(memory_root: Path) -> Iterator[None]:
    root = Path(memory_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "lifecycle.lock"
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def load_persisted(memory_root: Path) -> dict[str, Any]:
    """Read the lifecycle sidecar, returning a normalised dict.

    Missing file → empty dict (caller treats as fresh project).
    Malformed file → raises :class:`LifecycleIOError` so the caller can
    decide whether to recover (typically: log + treat as fresh).
    """
    path = lifecycle_path(memory_root)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LifecycleIOError(f"cannot read {path}: {exc}") from exc
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LifecycleIOError(f"malformed JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LifecycleIOError(f"top-level JSON in {path} is not an object")
    return payload


def apply_persisted_to_status(
    status: ProjectStatus, persisted: dict[str, Any]
) -> ProjectStatus:
    """Overlay any persisted fields (state, last_state_change_at,
    consecutive_no_progress_ticks) onto the observed status.

    The persisted state wins for ``state`` — that's the whole point of
    persisting it (a quarantined project should stay quarantined across
    daemon restarts even if the observable signals say it could run).
    All other fields are observed.
    """
    if not persisted:
        return status

    state_str = persisted.get("state")
    if state_str:
        try:
            status = _replace(status, state=ProjectState(state_str))
        except ValueError:
            # Unknown state value — fall through, keep observed state.
            pass

    last_change = _parse_iso(persisted.get("last_state_change_at"))
    if last_change is not None:
        status = _replace(status, last_state_change_at=last_change)

    no_progress = persisted.get("consecutive_no_progress_ticks")
    if isinstance(no_progress, int) and no_progress >= 0:
        status = _replace(status, consecutive_no_progress_ticks=no_progress)
    return status


def _replace(status: ProjectStatus, **changes: Any) -> ProjectStatus:
    from dataclasses import replace

    return replace(status, **changes)


def write_persisted(
    memory_root: Path,
    *,
    status: ProjectStatus,
    history: list[LifecycleEvent] | None = None,
) -> Path:
    """Write the lifecycle sidecar atomically. Returns path written.

    ``history`` is capped at the most recent ``_HISTORY_MAX`` events.
    """
    with _lifecycle_lock(memory_root):
        return _write_persisted_unlocked(
            memory_root,
            status=status,
            history=history,
        )


def _write_persisted_unlocked(
    memory_root: Path,
    *,
    status: ProjectStatus,
    history: list[LifecycleEvent] | None = None,
) -> Path:
    path = lifecycle_path(memory_root)
    history = (history or [])[-_HISTORY_MAX:]
    payload = {
        "state": status.state.value,
        "last_state_change_at": (
            status.last_state_change_at.isoformat()
            if status.last_state_change_at
            else None
        ),
        "consecutive_no_progress_ticks": int(
            status.consecutive_no_progress_ticks
        ),
        "history": [e.to_dict() for e in history],
    }
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return path


def append_event(
    memory_root: Path,
    *,
    new_status: ProjectStatus,
    event: LifecycleEvent,
) -> Path:
    """Convenience: load history, append the event, write back atomically.

    Tolerates a corrupt sidecar by starting fresh — never crashes the
    supervisor on bad persisted state.
    """
    with _lifecycle_lock(memory_root):
        try:
            persisted = load_persisted(memory_root)
        except LifecycleIOError:
            persisted = {}
        history_raw = persisted.get("history") or []
        history: list[LifecycleEvent] = []
        for entry in history_raw:
            if not isinstance(entry, dict):
                continue
            try:
                history.append(
                    LifecycleEvent(
                        at=_parse_iso(entry.get("at")) or datetime.now(timezone.utc),
                        from_state=ProjectState(entry["from_state"]),
                        to_state=ProjectState(entry["to_state"]),
                        reason=str(entry.get("reason", "")),
                    )
                )
            except (KeyError, ValueError):
                continue
        history.append(event)
        return _write_persisted_unlocked(
            memory_root,
            status=new_status,
            history=history,
        )


def resume_atomically_if_done(
    memory_root: Path,
    *,
    new_status: "ProjectStatus",
    event: "LifecycleEvent",
) -> bool:
    """Atomically write the resumed state only if the persisted state is still ``done``.

    Takes the lifecycle file lock, re-reads the persisted state inside the
    critical section, and writes only if the state has not changed since the
    caller last inspected it.  This makes the check → write a single critical
    section and prevents a TOCTOU race where two concurrent dispatches (e.g.
    two simultaneous ``manager_message`` calls) both observe ``done`` and both
    attempt to write a resumed state.

    Returns ``True`` if the transition was written, ``False`` if the state was
    already non-``done`` (idempotent — a concurrent caller already resumed,
    which is the correct outcome for the caller to treat as a no-op).
    """
    with _lifecycle_lock(memory_root):
        try:
            persisted = load_persisted(memory_root)
        except LifecycleIOError:
            persisted = {}
        if str(persisted.get("state") or "") != "done":
            return False
        history_raw = persisted.get("history") or []
        history: list[LifecycleEvent] = []
        for entry in history_raw:
            if not isinstance(entry, dict):
                continue
            try:
                history.append(
                    LifecycleEvent(
                        at=_parse_iso(entry.get("at")) or datetime.now(timezone.utc),
                        from_state=ProjectState(entry["from_state"]),
                        to_state=ProjectState(entry["to_state"]),
                        reason=str(entry.get("reason", "")),
                    )
                )
            except (KeyError, ValueError):
                continue
        history.append(event)
        _write_persisted_unlocked(memory_root, status=new_status, history=history)
        return True


def load_history(memory_root: Path) -> list[LifecycleEvent]:
    """Return all persisted events (most recent last). Empty list if
    no file / unreadable / no history."""
    try:
        persisted = load_persisted(memory_root)
    except LifecycleIOError:
        return []
    history_raw = persisted.get("history") or []
    out: list[LifecycleEvent] = []
    for entry in history_raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                LifecycleEvent(
                    at=_parse_iso(entry.get("at")) or datetime.now(timezone.utc),
                    from_state=ProjectState(entry["from_state"]),
                    to_state=ProjectState(entry["to_state"]),
                    reason=str(entry.get("reason", "")),
                )
            )
        except (KeyError, ValueError):
            continue
    return out
