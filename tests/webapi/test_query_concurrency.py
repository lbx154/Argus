"""Slow snapshot scans must leave HTTP control workers available."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress

import anyio
import httpx
import pytest

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi import server
from argus_skill.webapi.index_cache import (
    CacheWaitTimeout,
    IndexCache,
    QueryExecutor,
    QueryLimits,
    QueryUnavailable,
)


async def _until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _project(root):
    sid = "s-query-load"
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), display_name="Load probe"))
    return sid


def test_slow_distinct_http_queries_do_not_starve_daemon_stop(tmp_path, monkeypatch):
    sid = _project(tmp_path)
    release = threading.Event()
    active = 0
    peak = 0
    started = []
    stopped = []
    lock = threading.Lock()

    def slow_scan(label, result):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            started.append((label, threading.current_thread().name))
        try:
            assert release.wait(timeout=5), "test did not release query workers"
            return result
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(server, "list_projects", lambda **kw: slow_scan("index", []))
    monkeypatch.setattr(server, "list_project_costs", lambda **kw: slow_scan("costs", []))
    monkeypatch.setattr(server, "list_trashed_projects", lambda **kw: slow_scan("trash", []))
    monkeypatch.setattr(server, "build_snapshot", lambda *args, **kw: slow_scan("snapshot", {"sid": sid}))

    def stop(project_id, **kwargs):
        stopped.append((project_id, threading.current_thread().name))
        return {"rc": 0}

    monkeypatch.setattr(server, "stop_project_daemon", stop)
    app = server.create_app(
        global_root=tmp_path,
        query_limits=QueryLimits(workers=2, queued=2, waiters=32, timeout_seconds=2),
    )

    async def exercise():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        tasks = []
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://argus.test") as client:
                    urls = [
                        "/api/projects?limit=1", f"/api/projects/{sid}/snapshot?events_limit=1",
                        "/api/projects/costs?limit=1", "/api/trash",
                        *[f"/api/projects?limit={limit}" for limit in range(2, 10)],
                    ]
                    tasks = [asyncio.create_task(client.get(url)) for url in urls]
                    await _until(lambda: len(started) == 2 and sum(task.done() for task in tasks) >= 8)
                    before = time.monotonic()
                    response = await asyncio.wait_for(
                        client.post(f"/api/projects/{sid}/daemon/stop"), timeout=0.75,
                    )
                    assert response.status_code == 200
                    assert response.json()["rc"] == 0
                    assert time.monotonic() - before < 0.75
                    assert not release.is_set()
                    assert len(started) == peak == 2
                    assert all(name.startswith("argus-query") for _, name in started)
                    assert stopped and not stopped[0][1].startswith("argus-query")
                    release.set()
                    responses = await asyncio.gather(*tasks)
                    assert sum(response.status_code == 200 for response in responses) == 4
                    assert sum(response.status_code == 503 for response in responses) == 8
                    assert all(response.headers["Retry-After"] == "1" for response in responses if response.status_code == 503)
                    assert peak == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            limiter.total_tokens = previous

    asyncio.run(exercise())


def test_http_leader_and_waiter_timeout_keep_one_scan_for_later_readers(tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    calls = []

    def scan(**kwargs):
        calls.append(True)
        started.set()
        assert release.wait(timeout=3)
        return []

    monkeypatch.setattr(server, "list_projects", scan)
    app = server.create_app(
        global_root=tmp_path,
        query_limits=QueryLimits(workers=1, queued=0, timeout_seconds=0.06),
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://argus.test") as client:
                    leader = asyncio.create_task(client.get("/api/projects"))
                    await _until(started.is_set)
                    waiter = asyncio.create_task(client.get("/api/projects"))
                    for response in await asyncio.gather(leader, waiter):
                        assert response.status_code == 503
                        assert response.headers["Retry-After"] == "1"
                        assert "timed out" in response.json()["detail"]
                    assert (await client.get("/api/projects")).status_code == 503
                    assert len(calls) == 1
                    release.set()
                    assert (await client.get("/api/projects")).status_code == 200
                    assert len(calls) == 1
            finally:
                release.set()

    asyncio.run(exercise())


def test_http_cancelled_request_does_not_cancel_peer_and_app_reclaims_pool(tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    calls, workers = [], []

    def scan(**kwargs):
        calls.append(True)
        workers.append(threading.current_thread())
        started.set()
        assert release.wait(timeout=3)
        return []

    monkeypatch.setattr(server, "list_projects", scan)
    app = server.create_app(global_root=tmp_path, query_limits=QueryLimits(workers=1, queued=0))

    async def exercise():
        async with app.router.lifespan_context(app):
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://argus.test") as client:
                    abandoned = asyncio.create_task(client.get("/api/projects"))
                    await _until(started.is_set)
                    peer = asyncio.create_task(client.get("/api/projects"))
                    abandoned.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await abandoned
                    release.set()
                    assert (await peer).status_code == 200
                    assert len(calls) == 1
            finally:
                release.set()
        with pytest.raises(QueryUnavailable, match="shutting down"):
            app.state.query_executor.submit(lambda: None)

    asyncio.run(exercise())
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()


@pytest.mark.parametrize("ttl", [0, 5])
def test_http_deadlines_and_request_cancellation_do_not_duplicate_scan(ttl):
    release, started = threading.Event(), threading.Event()
    calls = []
    cache = IndexCache(ttl_seconds=ttl)
    executor = QueryExecutor(QueryLimits(workers=1, queued=0, timeout_seconds=0.05))

    def scan():
        calls.append(True)
        started.set()
        assert release.wait(timeout=3)
        return "ready"

    async def exercise():
        first = asyncio.create_task(cache.get_async("project", scan, executor=executor))
        try:
            await _until(started.is_set)
            peer = asyncio.create_task(cache.get_async("project", scan, executor=executor))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(CacheWaitTimeout):
                await peer
            # Both the first request and later waiters have bounded waits.
            with pytest.raises(CacheWaitTimeout):
                await cache.get_async("project", scan, executor=executor)
            assert len(calls) == 1
            survivor = asyncio.create_task(cache.get_async("project", scan, executor=executor))
            await asyncio.sleep(0)
            release.set()
            assert await survivor == "ready"
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await first
            await executor.close()

    asyncio.run(exercise())


def test_async_cache_invalidation_cannot_reinstall_an_old_result():
    started, release = threading.Event(), threading.Event()
    cache = IndexCache(ttl_seconds=60)
    executor = QueryExecutor(QueryLimits(workers=2, queued=0, timeout_seconds=1))

    def old_scan():
        started.set()
        assert release.wait(timeout=3)
        return "old"

    async def exercise():
        old = asyncio.create_task(cache.get_async("project", old_scan, executor=executor))
        try:
            await _until(started.is_set)
            cache.invalidate()
            assert await cache.get_async("project", lambda: "new", executor=executor) == "new"
            release.set()
            assert await old == "old"
            assert await cache.get_async("project", lambda: pytest.fail("stale scan repopulated cache"), executor=executor) == "new"
        finally:
            release.set()
            await old
            await executor.close()

    asyncio.run(exercise())


def test_waiter_admission_is_bounded_without_cancelling_existing_request():
    release, started = threading.Event(), threading.Event()
    executor = QueryExecutor(QueryLimits(workers=1, queued=0, waiters=1, timeout_seconds=1))
    cache = IndexCache()

    def scan():
        started.set()
        assert release.wait(timeout=3)
        return "kept"

    async def exercise():
        leader = asyncio.create_task(cache.get_async("same", scan, executor=executor))
        try:
            await _until(started.is_set)
            with pytest.raises(QueryUnavailable, match="Too many"):
                await cache.get_async("same", scan, executor=executor)
            release.set()
            assert await leader == "kept"
        finally:
            release.set()
            await leader
            await executor.close()

    asyncio.run(exercise())


def test_shutdown_cancels_queued_scans_and_reclaims_workers_after_active_scan_finishes():
    started, release = threading.Event(), threading.Event()
    executor = QueryExecutor(QueryLimits(workers=1, queued=1, timeout_seconds=2, shutdown_seconds=0.03))
    cache = IndexCache()
    threads, queued_calls = [], []

    def running_scan():
        threads.append(threading.current_thread())
        started.set()
        assert release.wait(timeout=3)
        return "finished"

    async def exercise():
        running = asyncio.create_task(cache.get_async("running", running_scan, executor=executor))
        queued = None
        try:
            await _until(started.is_set)
            queued = asyncio.create_task(cache.get_async("queued", lambda: queued_calls.append(True), executor=executor))
            await asyncio.sleep(0)
            before = time.monotonic()
            await executor.close()
            assert time.monotonic() - before < 0.5
            with pytest.raises(QueryUnavailable, match="shutting down"):
                await queued
            with pytest.raises(QueryUnavailable, match="shutting down"):
                await cache.get_async("new", lambda: None, executor=executor)
            assert queued_calls == []
            release.set()
            assert await running == "finished"
        finally:
            release.set()
            await asyncio.gather(running, *([queued] if queued else []), return_exceptions=True)
            await executor.close()

    asyncio.run(exercise())
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()
