"""Failures and slow reads must not turn one shared scan into a retry storm."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.webapi import index_cache
from argus.webapi.index_cache import IndexCache


def _cohort(cache, monkeypatch, count):
    """Release a scan only after all callers joined the same in-flight entry."""
    claim = cache._claim
    joined = 0
    lock = threading.Lock()
    everyone = threading.Event()

    def counted(key):
        nonlocal joined
        result = claim(key)
        with lock:
            joined += 1
            if joined == count:
                everyone.set()
        return result

    monkeypatch.setattr(cache, "_claim", counted)
    return everyone


def test_failed_scan_is_shared_by_all_waiters_without_implicit_retries(monkeypatch):
    cache = IndexCache(ttl_seconds=5)
    count = 40
    everyone = _cohort(cache, monkeypatch, count)
    release = threading.Event()
    scans = 0
    counter_lock = threading.Lock()

    def unavailable():
        nonlocal scans
        with counter_lock:
            scans += 1
        assert release.wait(timeout=5)
        raise OSError("snapshot source unavailable")

    def poll():
        with pytest.raises(OSError, match="snapshot source unavailable"):
            cache.get("same-project", unavailable)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(poll) for _ in range(count)]
        try:
            assert everyone.wait(timeout=5)
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)

    assert scans == 1
    # Failure belongs to the completed flight, not the cache or a later request.
    assert cache.get("same-project", lambda: "recovered") == "recovered"


def test_wait_timeout_does_not_start_duplicate_scans_or_cancel_the_leader(monkeypatch):
    cache = IndexCache(ttl_seconds=5)
    monkeypatch.setattr(index_cache, "_LEADER_WAIT_TIMEOUT_SECONDS", 0.03)
    started = threading.Event()
    release = threading.Event()
    scans = 0
    counter_lock = threading.Lock()

    def slow_scan():
        nonlocal scans
        with counter_lock:
            scans += 1
        started.set()
        assert release.wait(timeout=5)
        return ["finished"]

    def wait_for_scan():
        with pytest.raises(TimeoutError, match="Snapshot refresh timed out"):
            cache.get("slow-project", slow_scan)

    with ThreadPoolExecutor(max_workers=9) as pool:
        leader = pool.submit(cache.get, "slow-project", slow_scan)
        try:
            assert started.wait(timeout=5)
            waiters = [pool.submit(wait_for_scan) for _ in range(8)]
            # Other keys can still make progress while this read is blocked.
            assert cache.get("other-project", lambda: "ready") == "ready"
            for waiter in waiters:
                waiter.result(timeout=1)
            assert scans == 1
        finally:
            release.set()
        assert leader.result(timeout=5) == ["finished"]
    assert cache.get("slow-project", lambda: pytest.fail("leader result was lost")) == ["finished"]


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_ttl_cannot_make_a_mutable_snapshot_permanent(value):
    assert index_cache.resolve_ttl_seconds({index_cache.TTL_ENV_VAR: str(value)}) == 0
    assert index_cache.resolve_snapshot_ttl_seconds({index_cache.SNAPSHOT_TTL_ENV_VAR: str(value)}) == 0
    cache = IndexCache(ttl_seconds=value)
    assert not cache.enabled
