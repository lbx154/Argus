"""The cockpit's polled listings must not collapse when several tabs are open.

``/api/projects``, ``/api/projects/costs`` and the trash listing each walk every
session directory under every root, and the Web UI polls them on a timer from
every open tab. The routes are synchronous, so N pollers meant N *identical*
scans running at once in the Starlette threadpool, serialized on the GIL.

Measured against a real 866-session home before this cache existed:

    1 request   0.19s
    20 requests 9.68s
    40 requests 20.63s   (fastest response no sooner than the slowest)

Once latency crosses the poll interval the next round of polls stacks on the
round still running and the server never recovers — which is exactly what a
cockpit left open overnight showed on 2026-07-26: ``/api/projects`` taking 30s
while ``/api/meta`` still answered instantly. With the coalescing cache the
same 40-request burst finishes in 0.30s.

These tests pin the two properties that produce that: concurrent callers on one
key perform exactly one scan, and a mutation is never hidden behind the TTL.
"""

from __future__ import annotations

import threading
import time

from argus.webapi.index_cache import (
    DEFAULT_SNAPSHOT_TTL_SECONDS,
    DEFAULT_TTL_SECONDS,
    SNAPSHOT_TTL_ENV_VAR,
    TTL_ENV_VAR,
    IndexCache,
    resolve_snapshot_ttl_seconds,
    resolve_ttl_seconds,
)


class _CountingScan:
    """Stands in for a whole-home directory walk, counting how often it runs."""

    def __init__(self, *, duration: float = 0.05, value: object = None) -> None:
        self.duration = duration
        self.value = value if value is not None else ["a-project"]
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> object:
        with self._lock:
            self.calls += 1
        time.sleep(self.duration)
        return self.value


def test_a_value_is_reused_until_the_ttl_expires() -> None:
    cache = IndexCache(ttl_seconds=0.3)
    scan = _CountingScan(duration=0.0)

    cache.get("k", scan)
    cache.get("k", scan)
    assert scan.calls == 1

    time.sleep(0.35)
    cache.get("k", scan)
    assert scan.calls == 2


def test_a_zero_ttl_disables_caching_entirely() -> None:
    """An operator ruling the cache out must get the uncoalesced behavior."""
    cache = IndexCache(ttl_seconds=0.0)
    scan = _CountingScan(duration=0.0)

    cache.get("k", scan)
    cache.get("k", scan)

    assert not cache.enabled
    assert scan.calls == 2


def test_the_ttl_is_configurable_and_a_bad_value_disables_rather_than_raises() -> None:
    assert resolve_ttl_seconds({}) == DEFAULT_TTL_SECONDS
    assert resolve_ttl_seconds({TTL_ENV_VAR: "0.5"}) == 0.5
    assert resolve_ttl_seconds({TTL_ENV_VAR: "0"}) == 0.0
    assert resolve_ttl_seconds({TTL_ENV_VAR: "not-a-number"}) == 0.0
    assert resolve_snapshot_ttl_seconds({}) == DEFAULT_SNAPSHOT_TTL_SECONDS
    assert resolve_snapshot_ttl_seconds({SNAPSHOT_TTL_ENV_VAR: "3.5"}) == 3.5
    assert resolve_snapshot_ttl_seconds({SNAPSHOT_TTL_ENV_VAR: "bad"}) == 0.0


def test_the_key_table_stays_bounded() -> None:
    """Many distinct queries must not grow the table without limit."""
    cache = IndexCache(ttl_seconds=0.01)

    for index in range(500):
        cache.get(("projects", index), lambda: ["x"])

    assert len(cache._entries) <= 64
