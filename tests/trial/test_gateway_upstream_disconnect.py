"""Upstream disconnect observation and bounded watcher ownership."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from argus.trial.gateway import create_app, prepare
from argus.trial.gateway_accounting import GatewayAccounting, RequestMonitor
from argus.trial.gateway_observation import GatewayAttempt
from argus.trial.store import Store
from tests.trial.test_gateway_billing_lifecycle import asgi_request, issue, offline_settings
from tests.trial.test_gateway_billing_responsiveness import KEY_ID, PAYLOAD, response_data


@pytest.mark.parametrize("phase", ["authorization", "headers", "json-body"])
@pytest.mark.parametrize("termination", ["disconnect", "cancel"])
def test_upstream_wait_ends_before_barrier_release(tmp_path, phase, termination):
    settings = offline_settings(tmp_path)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, closed = [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await release.wait()
                yield json.dumps(response_data()).encode()

            async def aclose(self):
                closed.append(True)

        async def provider(request):
            calls.append(request)
            if phase == "headers":
                entered.set()
                await release.wait()
            if phase == "json-body":
                return httpx.Response(200, stream=Body())
            return httpx.Response(200, json=response_data())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            if phase == "authorization":
                original = app.state.copilot.authorization

                async def authorization():
                    entered.set()
                    await release.wait()
                    return await original()

                app.state.copilot.authorization = authorization
            task, disconnected, messages = asgi_request(app, credential)
            try:
                await asyncio.wait_for(entered.wait(), 1)
                start = time.monotonic()
                if termination == "disconnect":
                    disconnected.set()
                    await asyncio.wait_for(asyncio.shield(task), 0.3)
                    assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [499]
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(task), 0.3)
                assert time.monotonic() - start < 0.3
                assert not release.is_set() and app.state.request_slots._value == 10
                await asyncio.wait_for(app.state.accounting.wait_idle(), 1)
                assert app.state.accounting.pending == 0
                status = app.state.store.status(KEY_ID)
                assert status["active_requests"] == 0
                assert status["tokens_used"] == (0 if phase == "authorization" else prepare(PAYLOAD, settings.model)[1])
                assert len(calls) == (0 if phase == "authorization" else 1)
                assert len(closed) == (1 if phase == "json-body" else 0)
                with app.state.store.transaction() as db:
                    row, = db.execute("SELECT * FROM trial_gateway_attempts").fetchall()
                    assert row["outcome"] == ("disconnected" if termination == "disconnect" else "cancelled")
                    assert row["selected_response_status"] == (499 if termination == "disconnect" else None)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("trigger", ["disconnect-before-headers-return", "cancel-before-handoff"])
def test_headers_to_sse_handoff_keeps_response_and_cleanup_owned(tmp_path, monkeypatch, trigger):
    settings = offline_settings(tmp_path)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed, iterated = [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                iterated.append(True)
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                closed.append(True)

        async def provider(request):
            response = httpx.Response(200, stream=Body())
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # The headers may finish just as the watcher cancels us. A
                # transport returning this late response must not orphan it.
                pass
            return response

        if trigger == "cancel-before-handoff":
            original = GatewayAttempt.streaming

            def streaming(attempt):
                original(attempt)
                # This runs after handed_off=True, before the response object
                # is returned. No await may strand ownership in this window.
                asyncio.current_task().cancel()

            monkeypatch.setattr(GatewayAttempt, "streaming", streaming)
        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            payload = {**PAYLOAD, "stream": True}
            task, disconnected, _ = asgi_request(app, credential, payload=payload)
            try:
                await asyncio.wait_for(entered.wait(), 1)
                if trigger == "disconnect-before-headers-return":
                    disconnected.set()
                release.set()
                result, = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 0.5)
                if trigger == "cancel-before-handoff":
                    assert isinstance(result, asyncio.CancelledError)
                else:
                    assert result is None
                    assert not iterated
                await asyncio.wait_for(app.state.accounting.wait_idle(), 1)
                assert closed == [True]
                assert app.state.request_slots._value == 10
                assert app.state.accounting.pending == 0
                assert app.state.store.status(KEY_ID)["tokens_uncertain"] == prepare(payload, settings.model)[1]
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("started", [False, True])
def test_watcher_stop_collects_unstarted_or_swallowed_cancellation(tmp_path, started):
    async def run():
        entered, swallowed = asyncio.Event(), asyncio.Event()
        polls = []

        class Request:
            async def is_disconnected(self):
                if asyncio.current_task().get_name() == "argus-upstream-disconnect":
                    polls.append(True)
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        # Model is_disconnected's AnyIO cancellation scope
                        # consuming task cancellation and returning False.
                        swallowed.set()
                return False

        store = Store(tmp_path / "usage.sqlite3")
        store.issue("key", "offline")
        accounting = GatewayAccounting(store, 1)
        monitor = RequestMonitor(Request(), accounting)
        lease = await accounting.acquire(monitor)
        lease.request_id = await monitor.wait(lease.reserve("key", 100))
        monitor.start_disconnect_watch()
        if started:
            await asyncio.wait_for(entered.wait(), 1)
        cleanup = lease.finish()
        monitor.done()
        await asyncio.wait_for(asyncio.shield(cleanup), 0.5)
        await accounting.wait_idle()
        assert monitor._watcher.done() and monitor._watcher_done.done()
        assert swallowed.is_set() == started and len(polls) == int(started)
        assert accounting.pending == 0 and accounting._slots._value == 1
        assert store.status("key")["tokens_used"] == 0
        await accounting.close()

    asyncio.run(run())


def test_lease_capacity_includes_a_watcher_still_unwinding_cancel(tmp_path):
    async def run():
        entered, stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Request:
            async def is_disconnected(self):
                if asyncio.current_task().get_name() == "argus-upstream-disconnect":
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        stopped.set()
                        await release.wait()
                return False

        store = Store(tmp_path / "usage.sqlite3")
        store.issue("key", "offline")
        accounting = GatewayAccounting(store, 1)
        monitor = RequestMonitor(Request(), accounting)
        lease = await accounting.acquire(monitor)
        lease.request_id = await monitor.wait(lease.reserve("key", 100))
        monitor.start_disconnect_watch()
        await asyncio.wait_for(entered.wait(), 1)
        cleanup = lease.finish()
        monitor.done()
        await asyncio.wait_for(stopped.wait(), 1)
        acquired = asyncio.Event()

        async def next_request():
            next_monitor = RequestMonitor(Request(), accounting)
            try:
                next_lease = await accounting.acquire(next_monitor)
                acquired.set()
                await next_lease.wait_settled()
            finally:
                next_monitor.done()

        waiter = asyncio.create_task(next_request())
        try:
            async with asyncio.timeout(1):
                while not accounting._waiters:
                    await asyncio.sleep(0)
            assert not acquired.is_set()
            assert accounting.pending == 1 and accounting._slots._value == 0
            assert not cleanup.done() and not monitor._watcher_done.done()
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(cleanup, waiter), 1)
            await accounting.close()
        assert acquired.is_set() and accounting.pending == 0
        assert accounting._slots._value == 1 and monitor._watcher_done.done()

    asyncio.run(run())
