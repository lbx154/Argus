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

import pytest

from argus_skill.webapi.index_cache import (
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


def test_concurrent_callers_on_one_key_scan_once() -> None:
    """The property that stops the poll storm: N pollers, one scan."""
    cache = IndexCache(ttl_seconds=5.0)
    scan_started = threading.Event()
    release_scan = threading.Event()
    scan_calls = 0
    scan_lock = threading.Lock()
    results: list[object] = []
    results_lock = threading.Lock()

    def scan() -> list[str]:
        nonlocal scan_calls
        with scan_lock:
            scan_calls += 1
        scan_started.set()
        assert release_scan.wait(timeout=5.0)
        return ["a-project"]

    def poll() -> None:
        value = cache.get(("projects", 100, False), scan)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=poll) for _ in range(40)]
    for thread in threads:
        thread.start()
    assert scan_started.wait(timeout=5.0)
    release_scan.set()
    for thread in threads:
        thread.join()

    assert scan_calls == 1
    assert len(results) == 40
    assert all(value == ["a-project"] for value in results)


def test_distinct_keys_are_not_shared() -> None:
    """Coalescing must not serve one query's answer to a different query."""
    cache = IndexCache(ttl_seconds=5.0)

    first = cache.get(("projects", 100, False), lambda: ["hundred"])
    second = cache.get(("projects", 5, False), lambda: ["five"])
    third = cache.get(("projects", 100, True), lambda: ["include-empty"])

    assert (first, second, third) == (["hundred"], ["five"], ["include-empty"])


def test_a_value_is_reused_until_the_ttl_expires() -> None:
    cache = IndexCache(ttl_seconds=0.3)
    scan = _CountingScan(duration=0.0)

    cache.get("k", scan)
    cache.get("k", scan)
    assert scan.calls == 1

    time.sleep(0.35)
    cache.get("k", scan)
    assert scan.calls == 2


def test_a_failed_scan_is_not_cached() -> None:
    """A directory that vanished mid-walk must be retried, not remembered."""
    cache = IndexCache(ttl_seconds=5.0)
    attempts: list[int] = []

    def flaky() -> list[str]:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("session dir vanished mid-walk")
        return ["recovered"]

    with pytest.raises(OSError):
        cache.get("k", flaky)

    assert cache.get("k", flaky) == ["recovered"]


def test_waiters_see_the_leaders_failure_and_then_recover() -> None:
    """A failing leader must not strand the callers sharing its flight."""
    cache = IndexCache(ttl_seconds=5.0)
    release = threading.Event()
    attempts: list[int] = []
    attempts_lock = threading.Lock()
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def flaky() -> list[str]:
        with attempts_lock:
            attempts.append(1)
            first = len(attempts) == 1
        if first:
            release.wait(timeout=5.0)
            raise OSError("scan failed")
        return ["recovered"]

    def poll() -> None:
        try:
            value = cache.get("k", flaky)
        except OSError:
            outcome = "raised"
        else:
            outcome = "ok" if value == ["recovered"] else "wrong"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=poll) for _ in range(6)]
    for thread in threads:
        thread.start()
        time.sleep(0.01)
    release.set()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not any(thread.is_alive() for thread in threads)
    assert len(outcomes) == 6
    assert "wrong" not in outcomes
    # The leader's caller sees the real error rather than a silent empty list.
    assert "raised" in outcomes
    # Everyone else recovers instead of inheriting a cached failure.
    assert outcomes.count("ok") >= 1


def test_invalidate_drops_cached_values() -> None:
    cache = IndexCache(ttl_seconds=60.0)
    scan = _CountingScan(duration=0.0)

    cache.get("k", scan)
    cache.invalidate()
    cache.get("k", scan)

    assert scan.calls == 2


def test_invalidate_detaches_an_in_flight_stale_scan() -> None:
    """A mutation must keep later callers from joining a pre-mutation flight."""
    cache = IndexCache(ttl_seconds=60.0)
    old_started = threading.Event()
    release_old = threading.Event()
    old_results: list[object] = []

    def old_scan() -> list[str]:
        old_started.set()
        assert release_old.wait(timeout=5.0)
        return ["old"]

    old_thread = threading.Thread(target=lambda: old_results.append(cache.get("k", old_scan)))
    old_thread.start()
    assert old_started.wait(timeout=5.0)

    cache.invalidate()
    assert cache.get("k", lambda: ["new"]) == ["new"]

    release_old.set()
    old_thread.join(timeout=5.0)
    assert not old_thread.is_alive()
    assert old_results == [["old"]]
    assert cache.get("k", lambda: ["wrong"]) == ["new"]


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
