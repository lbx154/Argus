"""Coalescing caches for expensive read-only data the cockpit polls.

The project index, the per-project cost roll-up and the trash listing each walk
every session directory under every root. One such scan is cheap (~0.1s for a
few hundred sessions), but the Web UI polls them on a timer from every open tab
and the routes are synchronous, so N clients means N *identical* scans running
concurrently in the Starlette threadpool.

That is worse than N times the work. The scans are pure Python, so they
serialize on the GIL while still paying the context-switching, and none of them
can finish early: measured on a real 866-session home, 1 request took 0.12s,
5 concurrent took 1.6s, 20 took 8.7s and 40 took 19.3s — with the fastest
response in each round finishing no sooner than the slowest. Once latency
crosses the poll interval the next round stacks on top of the round still
running and the server never recovers; that is how a cockpit with a handful of
open tabs ends up taking 30s to answer ``/api/projects`` and starves every
other synchronous route of a worker thread. Per-project snapshots have the same
failure mode: they aggregate event history, spend, provider usage, daemon state,
mission view and host metrics, while the TUI starts a new poll every five seconds.

This module is the dumb pipe that fixes it: concurrent callers asking for the
same key share a single computation ("single flight"), and the result is reused
for a short TTL so an unsynchronized poll storm still costs one scan. It makes
no decisions about *what* is being listed — it only stops the server doing the
same work many times over.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

DEFAULT_TTL_SECONDS = 2.0
TTL_ENV_VAR = "ARGUS_WEB_INDEX_CACHE_TTL"
DEFAULT_SNAPSHOT_TTL_SECONDS = 5.0
SNAPSHOT_TTL_ENV_VAR = "ARGUS_WEB_SNAPSHOT_CACHE_TTL"

# A wedged leader must not pin every waiter forever. Waiters that hit this
# fall back to computing for themselves, which is the pre-cache behavior.
_LEADER_WAIT_TIMEOUT_SECONDS = 30.0

# Query parameters are bounded (``limit`` is 1..2000), but a caller can still
# mint many distinct keys. Keep the table small rather than trusting that.
_MAX_ENTRIES = 64


def resolve_ttl_seconds(environ: dict[str, str] | None = None) -> float:
    """Read the cache TTL from the environment.

    A non-positive or unparseable value disables caching, which restores the
    uncoalesced behavior for anyone who needs to rule the cache out.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(TTL_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def resolve_snapshot_ttl_seconds(environ: dict[str, str] | None = None) -> float:
    """Read the per-project snapshot TTL from the environment."""
    env = os.environ if environ is None else environ
    raw = str(env.get(SNAPSHOT_TTL_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_SNAPSHOT_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


class _Entry:
    __slots__ = ("value", "expires_at", "done", "error", "in_flight")

    def __init__(self) -> None:
        self.value: Any = None
        self.expires_at: float = 0.0
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.in_flight = True


class IndexCache:
    """Single-flight + short-TTL cache shared by one ``create_app`` instance."""

    def __init__(self, *, ttl_seconds: float | None = None) -> None:
        self.ttl_seconds = resolve_ttl_seconds() if ttl_seconds is None else max(0.0, ttl_seconds)
        self._lock = threading.Lock()
        self._entries: dict[Any, _Entry] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0.0

    def get(self, key: Any, compute: Callable[[], Any]) -> Any:
        """Return ``compute()`` for ``key``, sharing work with concurrent callers.

        The returned object is shared by every caller that hit the same key, so
        callers must treat it as read-only; the listings cached here are
        serialized straight to JSON and never mutated.

        Exceptions are propagated to every caller waiting on the same key and
        are never cached — a scan that failed because a directory vanished
        mid-walk must be retried, not remembered.
        """
        if not self.enabled:
            return compute()

        while True:
            entry, is_leader = self._claim(key)
            if entry is None:
                # Every retained slot is an active flight for another key.
                # Fail open rather than growing an attacker-controlled table.
                return compute()
            if is_leader:
                return self._run_as_leader(key, entry, compute)
            if not entry.done.wait(timeout=_LEADER_WAIT_TIMEOUT_SECONDS):
                return compute()
            if entry.error is not None:
                # The leader failed. Retry through the normal path so exactly
                # one of the waiters becomes the next leader.
                continue
            return entry.value

    def invalidate(self) -> None:
        """Drop every cached value.

        Called after a mutation so the next poll cannot show the operator a
        pre-mutation index and read as "my change did not take". Active flights
        are detached too: callers that joined them before the mutation may
        finish with their original snapshot, but a caller arriving afterwards
        must start a fresh scan and cannot repopulate the cache with stale data.
        """
        with self._lock:
            self._entries.clear()

    def _claim(self, key: Any) -> tuple[_Entry | None, bool]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.in_flight:
                    return entry, False
                if entry.expires_at > now:
                    return entry, False
                del self._entries[key]
            self._evict_expired(now)
            if len(self._entries) >= _MAX_ENTRIES:
                return None, False
            fresh = _Entry()
            self._entries[key] = fresh
            return fresh, True

    def _run_as_leader(self, key: Any, entry: _Entry, compute: Callable[[], Any]) -> Any:
        try:
            value = compute()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            entry.error = exc
            with self._lock:
                if self._entries.get(key) is entry:
                    del self._entries[key]
            entry.in_flight = False
            entry.done.set()
            raise
        entry.value = value
        entry.expires_at = time.monotonic() + self.ttl_seconds
        entry.in_flight = False
        entry.done.set()
        return value

    def _evict_expired(self, now: float) -> None:
        if len(self._entries) < _MAX_ENTRIES:
            return
        for key in [
            key
            for key, entry in self._entries.items()
            if not entry.in_flight and entry.expires_at <= now
        ]:
            del self._entries[key]
        if len(self._entries) < _MAX_ENTRIES:
            return
        for key in [key for key, entry in self._entries.items() if not entry.in_flight][
            : len(self._entries) - _MAX_ENTRIES + 1
        ]:
            del self._entries[key]
