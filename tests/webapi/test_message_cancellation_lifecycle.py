"""Route task cancellation must not detach a still-running Manager provider."""
from __future__ import annotations

import asyncio
import functools
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from argus.webapi import manager_bridge
from argus.webapi.routes import manager as manager_routes
from argus.webapi.routes.models import CancelMessageIn, MessageIn


def endpoints(root):
    app = FastAPI()
    starts = []
    context = SimpleNamespace(
        require_auth=lambda: None,
        project_root_or_404=lambda sid: root,
        resolve_or_404=lambda sid: root,
        daemon_services=SimpleNamespace(start=lambda *args, **kwargs: starts.append(True)),
    )
    manager_routes.register_manager_routes(app, context, SimpleNamespace())
    message = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/projects/{sid}/message")
    cancel = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/projects/{sid}/message/cancel")
    return message, cancel, starts


async def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "asynchronous route did not reach its expected boundary"
        await asyncio.sleep(0.001)


def test_native_asyncio_route_cancellation_keeps_the_running_provider_cancellable(tmp_path, monkeypatch):
    entered, release, left_provider = threading.Event(), threading.Event(), threading.Event()
    callbacks = []

    def provider(sid, text, *, cancelled, **kwargs):
        callbacks.append(cancelled)
        entered.set()
        try:
            assert release.wait(3)
            return {"kind": "task", "item": {"id": "old-task", "status": "pending"}}
        finally:
            left_provider.set()

    monkeypatch.setattr(manager_bridge, "manager_message", provider)
    message, cancel, starts = endpoints(tmp_path)

    async def scenario():
        request = asyncio.create_task(message("project", MessageIn(text="old request", request_id="request")))
        try:
            await wait_until(entered.is_set)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            # The HTTP coroutine ended, but its worker still owns admission and
            # the provider's cancellation event. A late Stop can reach it.
            receipt = await cancel("project", CancelMessageIn(request_id="request"))
            assert receipt["requested"] is True and receipt["active"] is True
            assert callbacks[0]() is True
            assert not left_provider.is_set()
            release.set()
            await wait_until(left_provider.is_set)
            for _ in range(1000):
                receipt = await cancel("project", CancelMessageIn(request_id="request"))
                if not receipt["active"]:
                    break
                await asyncio.sleep(0.001)
            assert receipt["active"] is False and receipt["status"] == "cancelled"
            assert starts == []
        finally:
            release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_route_rejects_a_worker_that_starts_after_http_abandonment(tmp_path, monkeypatch):
    queued = threading.Event()
    delayed_workers = []
    manager_calls = []

    async def delayed_threadpool(func, *args, **kwargs):
        delayed_workers.append(functools.partial(func, *args, **kwargs))
        queued.set()
        await asyncio.Future()

    monkeypatch.setattr(manager_routes, "run_in_threadpool", delayed_threadpool)
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *args, **kwargs: manager_calls.append(True))
    message, cancel, starts = endpoints(tmp_path)

    async def scenario():
        request = asyncio.create_task(message("project", MessageIn(text="queued request", request_id="queued")))
        await wait_until(queued.is_set)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        receipt = await cancel("project", CancelMessageIn(request_id="queued"))
        assert receipt["status"] == "cancelled" and receipt["active"] is False
        # Model an already-submitted pool job arriving after cancellation. Its
        # handoff guard must reject it before entering Manager code.
        await run_in_threadpool(delayed_workers[0])
        assert manager_calls == [] and starts == []

    asyncio.run(scenario())
