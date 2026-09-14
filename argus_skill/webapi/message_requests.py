"""Bounded, app-local cancellation identities for foreground Manager requests.

Register at HTTP intake, before attachment work, and finish the lease in the
worker's finally block. A lease can cross threads; it holds no lock or context
variable while application code runs. Request IDs identify one logical request
and should not be reused, including after the short retention window expires.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import TracebackType


class MessageRequestConflict(RuntimeError):
    status_code = 409


class MessageRequestCapacityError(RuntimeError):
    status_code = 503


class MessageRequestCancelled(RuntimeError):
    """A cancellation arrived before this request was admitted."""

    status_code = 409


@dataclass
class _ActiveRequest:
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class _Tombstone:
    status: str
    expires_at: float


@dataclass(frozen=True)
class MessageRequestLease:
    sid: str
    request_id: str
    _registry: MessageRequestRegistry = field(repr=False)
    _entry: _ActiveRequest = field(repr=False)

    def cancelled(self) -> bool:
        # The callback owns its Event directly. Cleanup of unrelated request
        # identities, or completion of a newer lease, cannot detach it.
        return self._entry.stop.is_set()

    def finish(self) -> None:
        """Release admission once, without changing any newer request."""
        self._registry._finish((self.sid, self.request_id), self._entry)

    def __enter__(self) -> MessageRequestLease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.finish()


class MessageRequestRegistry:
    """An in-memory registry owned by one WebAPI app instance.

    Active requests are never expired or evicted. Finishing moves the active
    entry into a terminal tombstone, preserving its reserved slot. Only expired
    tombstones are removed: overload rejects new admission/cancel-before-intake
    requests, rather than forgetting a cancellation that was acknowledged.
    """

    def __init__(
        self,
        *,
        max_active: int = 256,
        max_entries: int = 4096,
        tombstone_ttl: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_active < 1 or max_entries < 1:
            raise ValueError("request capacity must be positive")
        if not math.isfinite(tombstone_ttl) or tombstone_ttl <= 0:
            raise ValueError("tombstone retention must be finite and positive")
        self._max_active = max_active
        self._max_entries = max_entries
        self._tombstone_ttl = tombstone_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], _ActiveRequest] = {}
        self._tombstones: OrderedDict[tuple[str, str], _Tombstone] = OrderedDict()

    @staticmethod
    def _key(sid: str, request_id: str, *, generate: bool = False) -> tuple[str, str]:
        session = str(sid or "").strip()
        identity = str(request_id or "").strip()
        if not session or len(session) > 512:
            raise ValueError("session id must contain 1-512 characters")
        if not identity and generate:
            identity = uuid.uuid4().hex
        if not identity or len(identity) > 128:
            raise ValueError("request id must contain 1-128 characters")
        return session, identity

    def _prune(self, now: float) -> None:
        # Entries are inserted with monotonically increasing expiry times.
        while self._tombstones:
            key, record = next(iter(self._tombstones.items()))
            if record.expires_at > now:
                break
            del self._tombstones[key]

    def _require_capacity(self) -> None:
        if len(self._active) + len(self._tombstones) >= self._max_entries:
            raise MessageRequestCapacityError("Manager request cancellation registry is full")

    def begin(self, sid: str, request_id: str = "") -> MessageRequestLease:
        key = self._key(sid, request_id, generate=True)
        with self._lock:
            self._prune(self._clock())
            if key in self._active:
                raise MessageRequestConflict("This Manager request is already active")
            terminal = self._tombstones.get(key)
            if terminal is not None:
                if terminal.status == "cancelled":
                    raise MessageRequestCancelled("This Manager request was already cancelled")
                raise MessageRequestConflict("This Manager request has already finished")
            if len(self._active) >= self._max_active:
                raise MessageRequestCapacityError("Too many active Manager requests")
            self._require_capacity()
            entry = _ActiveRequest()
            self._active[key] = entry
        return MessageRequestLease(key[0], key[1], self, entry)

    def active(self, sid: str) -> list[dict[str, str]]:
        """Return cancellable foreground requests for one project.

        This read is intentionally independent of snapshot caching: a browser
        reload must be able to reattach its Stop control without replaying the
        original POST.
        """
        session = self._key(sid, "status")[0]
        with self._lock:
            return [
                {"request_id": request_id, "status": "running"}
                for (request_sid, request_id), entry in self._active.items()
                if request_sid == session and not entry.stop.is_set()
            ]

    def cancel(self, sid: str, request_id: str) -> dict[str, str | bool]:
        key = self._key(sid, request_id)
        with self._lock:
            now = self._clock()
            self._prune(now)
            active = self._active.get(key)
            if active is not None:
                active.stop.set()
                status = "cancelled"
            elif key in self._tombstones:
                status = self._tombstones[key].status
            else:
                self._require_capacity()
                status = "cancelled"
                self._tombstones[key] = _Tombstone(status, now + self._tombstone_ttl)
        return {"requested": status != "finished", "sid": key[0], "request_id": key[1], "status": status, "active": active is not None}

    def _finish(self, key: tuple[str, str], entry: _ActiveRequest) -> None:
        with self._lock:
            if self._active.get(key) is not entry:
                return
            now = self._clock()
            self._prune(now)
            del self._active[key]
            self._tombstones[key] = _Tombstone(
                "cancelled" if entry.stop.is_set() else "finished",
                now + self._tombstone_ttl,
            )

    @contextmanager
    def message_request(self, sid: str, request_id: str = "") -> Iterator[Callable[[], bool]]:
        lease = self.begin(sid, request_id)
        try:
            yield lease.cancelled
        finally:
            lease.finish()


__all__ = [
    "MessageRequestRegistry", "MessageRequestLease", "MessageRequestCancelled",
    "MessageRequestConflict", "MessageRequestCapacityError",
]
