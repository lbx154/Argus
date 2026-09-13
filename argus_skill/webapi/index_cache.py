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

import asyncio
import math
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_TTL_SECONDS = 2.0
TTL_ENV_VAR = "ARGUS_WEB_INDEX_CACHE_TTL"
DEFAULT_SNAPSHOT_TTL_SECONDS = 5.0
SNAPSHOT_TTL_ENV_VAR = "ARGUS_WEB_SNAPSHOT_CACHE_TTL"

# A timed-out reader leaves the shared scan running rather than duplicating it.
_LEADER_WAIT_TIMEOUT_SECONDS = 30.0

# Query parameters are bounded (``limit`` is 1..2000), but a caller can still
# mint many distinct keys. Keep the table small rather than trusting that.
_MAX_ENTRIES = 64


class CacheWaitTimeout(TimeoutError):
    """The shared read is still running; HTTP callers may retry later."""

    def __init__(self) -> None:
        super().__init__("Snapshot refresh timed out; retry shortly.")


class QueryUnavailable(RuntimeError):
    """This app cannot admit another expensive read at present."""


@dataclass(frozen=True, slots=True)
class QueryLimits:
    workers: int = 2
    queued: int = 6
    waiters: int = 64
    timeout_seconds: float = 10.0
    shutdown_seconds: float = 1.0

    def __post_init__(self) -> None:
        for name, minimum in (("workers", 1), ("queued", 0), ("waiters", 1)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"query {name} must be an integer >= {minimum}")
        for name in ("timeout_seconds", "shutdown_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or _finite_ttl(value) == 0:
                raise ValueError(f"query {name} must be finite and positive")


class QueryExecutor:
    """One app's bounded scan pool, separate from Starlette's control workers.

    Admission counts queued work as well as running work. Request cancellation
    never cancels a shared scan; shutdown cancels work that has not started.
    Python cannot interrupt a synchronous filesystem call already running: its
    worker exits when that call returns, without holding up app shutdown.
    """

    def __init__(self, limits: QueryLimits | None = None) -> None:
        self.limits = limits or QueryLimits()
        self._pool = ThreadPoolExecutor(
            max_workers=self.limits.workers, thread_name_prefix="argus-query",
        )
        self._lock = threading.Lock()
        self._closed = False
        self._futures: set[Future] = set()
        self._requests = threading.BoundedSemaphore(self.limits.waiters)

    @contextmanager
    def request(self):
        with self._lock:
            if self._closed:
                raise QueryUnavailable("Snapshot queries are shutting down.")
        if not self._requests.acquire(blocking=False):
            raise QueryUnavailable("Too many snapshot requests; retry shortly.")
        try:
            yield
        finally:
            self._requests.release()

    def submit(self, compute: Callable[[], Any]) -> Future:
        with self._lock:
            if self._closed:
                raise QueryUnavailable("Snapshot queries are shutting down.")
            if len(self._futures) >= self.limits.workers + self.limits.queued:
                raise QueryUnavailable("Snapshot query capacity is busy; retry shortly.")
            future = self._pool.submit(compute)
            self._futures.add(future)
        future.add_done_callback(self._finished)
        return future

    def _finished(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    async def wait(self, future: Future) -> Any:
        wrapped = asyncio.wrap_future(future)
        # A request may leave before the scan raises. Retrieve that exception
        # even then, while preserving it for every still-interested waiter.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        deadline = asyncio.timeout(self.limits.timeout_seconds)
        try:
            async with deadline:
                return await asyncio.shield(wrapped)
        except TimeoutError:
            if deadline.expired():
                raise CacheWaitTimeout() from None
            raise

    async def close(self) -> None:
        with self._lock:
            self._closed = True
            futures = list(self._futures)
        # Do not call blocking shutdown in the shared HTTP threadpool.
        self._pool.shutdown(wait=False, cancel_futures=True)
        if futures:
            wrapped = [asyncio.wrap_future(future) for future in futures]
            for future in wrapped:
                future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            await asyncio.wait(wrapped, timeout=self.limits.shutdown_seconds)


def _finite_ttl(value: str | float) -> float:
    """Invalid durations disable caching; mutable snapshots never live forever."""
    try:
        ttl = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return ttl if math.isfinite(ttl) and ttl > 0.0 else 0.0


def resolve_ttl_seconds(environ: dict[str, str] | None = None) -> float:
    """Read the cache TTL from the environment.

    A non-positive or unparseable value disables caching, which restores the
    uncoalesced behavior for anyone who needs to rule the cache out.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(TTL_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    return _finite_ttl(raw)


def resolve_snapshot_ttl_seconds(environ: dict[str, str] | None = None) -> float:
    """Read the per-project snapshot TTL from the environment."""
    env = os.environ if environ is None else environ
    raw = str(env.get(SNAPSHOT_TTL_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_SNAPSHOT_TTL_SECONDS
    return _finite_ttl(raw)


class _Entry:
    __slots__ = ("value", "expires_at", "done", "error", "in_flight", "result")

    def __init__(self) -> None:
        self.value: Any = None
        self.expires_at: float = 0.0
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.in_flight = True
        self.result: Future = Future()


class IndexCache:
    """Single-flight + short-TTL cache shared by one ``create_app`` instance."""

    def __init__(self, *, ttl_seconds: float | None = None) -> None:
        self.ttl_seconds = resolve_ttl_seconds() if ttl_seconds is None else _finite_ttl(ttl_seconds)
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

        A flight shares its failure with its current waiters. Failures are not
        cached: a later request can retry, but this cache never retries on behalf
        of waiting callers. Timed-out waiters leave the original scan running so
        a slow filesystem cannot turn one request burst into duplicate scans.
        """
        if not self.enabled:
            return compute()

        entry, is_leader = self._claim(key)
        if entry is None:
            # Every retained slot is an active flight for another key.
            # Bypass caching rather than growing an unbounded key table.
            return compute()
        if is_leader:
            return self._run_as_leader(key, entry, compute)
        if not entry.done.wait(timeout=_LEADER_WAIT_TIMEOUT_SECONDS):
            raise CacheWaitTimeout()
        if entry.error is not None:
            raise entry.error
        return entry.value


    async def get_async(
        self, key: Any, compute: Callable[[], Any], *, executor: QueryExecutor,
    ) -> Any:
        """Share a scan while HTTP callers await without occupying workers.

        The same entry table backs sync and async callers. Capacity exhaustion
        rejects a new scan; it never bypasses the dedicated execution budget.
        A zero TTL disables value reuse but keeps HTTP in-flight coalescing.
        """
        with executor.request():
            entry, is_leader = self._claim(key)
            if entry is None:
                raise QueryUnavailable("Snapshot query cache is busy; retry shortly.")
            if is_leader:
                try:
                    task = executor.submit(lambda: self._run_as_leader(key, entry, compute))
                except QueryUnavailable as exc:
                    self._fail_entry(key, entry, exc)
                    raise

                def cancelled_before_start(done: Future) -> None:
                    if done.cancelled():
                        self._fail_entry(key, entry, QueryUnavailable("Snapshot queries are shutting down."))

                task.add_done_callback(cancelled_before_start)
            return await executor.wait(entry.result)

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
            self._fail_entry(key, entry, exc)
            raise
        entry.value = value
        entry.expires_at = time.monotonic() + self.ttl_seconds
        entry.in_flight = False
        entry.done.set()
        entry.result.set_result(value)
        return value

    def _fail_entry(self, key: Any, entry: _Entry, error: BaseException) -> None:
        entry.error = error
        with self._lock:
            if self._entries.get(key) is entry:
                del self._entries[key]
        entry.in_flight = False
        entry.done.set()
        entry.result.set_exception(error)

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
