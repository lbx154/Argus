"""Delivery-guard helpers shared by the chat channels.

Feishu redelivers an event until the app acknowledges it, and a reconnecting
WebSocket can replay recent traffic, so an inbound handler that is not
idempotent will run the same ``/add`` twice. These are the three guards every
channel needs:

* :class:`EventDedup` — remember processed event ids for 24h.
* :func:`sender_allowed` — allowlist gate on the sender id.
* :func:`chat_lock` — serialize work per chat so two messages from the same
  operator can't interleave mid-command.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

# Retention bounds the in-memory/disk dedup window, never message processing time.
_RETENTION_SECONDS = 24 * 60 * 60


class EventDedup:
    """Event-id ledger persisted next to the rest of the project state.

    Entries older than 24h are pruned on write; that is far longer than any
    platform's redelivery window and keeps the file small without a scheduler.
    """

    def __init__(self, path: Path, *, retention_seconds: int = _RETENTION_SECONDS) -> None:
        self.path = Path(path)
        self.retention_seconds = retention_seconds
        self._lock = threading.Lock()
        self._seen: dict[str, int] | None = None

    def _load(self) -> dict[str, int]:
        if self._seen is not None:
            return self._seen
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._seen = {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            self._seen = {}
        return self._seen

    def _persist(self, seen: dict[str, int]) -> None:
        tmp: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp",
            )
            tmp = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(seen, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            tmp = None
        except OSError:
            # Losing the ledger costs at most one duplicate reply; never fatal.
            pass
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def seen(self, event_id: str | None) -> bool:
        """True if *event_id* was already handled. Records it when it wasn't.

        An empty id means the platform gave us nothing to key on, so the
        caller gets ``False`` and no dedup.
        """
        if not event_id:
            return False
        now = int(time.time())
        with self._lock:
            ledger = self._load()
            if event_id in ledger:
                return True
            cutoff = now - self.retention_seconds
            fresh = {k: v for k, v in ledger.items() if v >= cutoff}
            fresh[event_id] = now
            self._seen = fresh
            self._persist(fresh)
            return False


def sender_allowed(sender_id: str | None, allowlist: str | None) -> bool:
    """Gate on a comma-separated allowlist.

    An unset/blank allowlist allows everyone — the channel is already behind
    a bot the operator had to create and install, and demanding an open_id
    before the first message would make setup impossible (you learn your own
    id by messaging the bot).
    """
    entries = [item.strip() for item in (allowlist or "").split(",") if item.strip()]
    if not entries:
        return True
    if "*" in entries:
        return True
    return str(sender_id or "") in entries


_CHAT_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def chat_lock(chat_id: str) -> threading.Lock:
    """Get-or-create the serialization lock for *chat_id*."""
    key = str(chat_id or "")
    with _LOCKS_GUARD:
        lock = _CHAT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CHAT_LOCKS[key] = lock
        return lock
