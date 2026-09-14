"""Shutdown and stream timeouts retain accounting without holding HTTP open."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import portalocker
from cryptography.fernet import Fernet

from argus.trial.gateway import Settings, create_app, prepare
from argus.trial.secrets import Vault, write_private
from tests.trial.test_gateway_billing_responsiveness import (
    KEY_ID,
    PAYLOAD,
    HeldWriter,
    ledger_rows,
    request_scope,
    response_data,
)


def settings_for(tmp_path, *, timeout):
    state = tmp_path / "state"
    state.mkdir()
    key_file = tmp_path / "key"
    write_private(key_file, Fernet.generate_key())
    Vault(key_file, state / "github-token.enc").save("offline-shutdown-fixture-only")
    return Settings(state, key_file, timeout=timeout)


def lock_available(path):
    try:
        with portalocker.Lock(str(path), timeout=0):
            return True
    except portalocker.exceptions.LockException:
        return False


def asgi_exchange(payload, messages):
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": json.dumps(payload).encode()}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    return receive, send


def test_shutdown_keeps_lock_until_cancel_swallowing_send_and_late_close_finish(tmp_path):
    settings = settings_for(tmp_path, timeout=3)
    observation = {}

    async def run():
        entered = asyncio.Event()
        swallowed = asyncio.Event()
        release_provider = asyncio.Event()
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        calls = []
        closed = []
        iterated = []
        messages = []

        class LateStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                iterated.append(True)
                yield json.dumps(response_data()).encode()

            async def aclose(self):
                close_entered.set()
                await release_close.wait()
                closed.append(True)

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            calls.append(str(request.url))
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                swallowed.set()
                await release_provider.wait()
            return httpx.Response(200, stream=LateStream())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        store = app.state.store
        credential = app.state.vault.credential(KEY_ID)
        store.issue(KEY_ID, credential)
        receive, send = asgi_exchange(PAYLOAD, messages)
        request_task = asyncio.create_task(app(request_scope(credential), receive, send))
        shutdown_task = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            started = time.monotonic()
            shutdown_task = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.wait_for(swallowed.wait(), 1)
            await asyncio.sleep(0.05)
            observation["swallowed_cancel_after_seconds"] = time.monotonic() - started
            observation["shutdown_waited_for_late_response"] = not shutdown_task.done()
            observation["request_pending_before_provider_release"] = not request_task.done()
            observation["lock_held_before_provider_release"] = not lock_available(settings.state_dir / "gateway.lock")
            release_provider.set()
            await asyncio.wait_for(close_entered.wait(), 1)
            await asyncio.sleep(0.05)
            observation["shutdown_waited_for_late_close"] = not shutdown_task.done()
            observation["lock_held_before_close_release"] = not lock_available(settings.state_dir / "gateway.lock")
            observation["billing_capacity_retained_during_close"] = app.state.accounting.pending
            release_close.set()
            await asyncio.wait_for(asyncio.shield(shutdown_task), 2)
            result, = await asyncio.gather(request_task, return_exceptions=True)
            observation["request_cancelled"] = isinstance(result, asyncio.CancelledError)
            observation["lock_released_after_shutdown"] = lock_available(settings.state_dir / "gateway.lock")
            observation["provider_calls"] = len(calls)
            observation["late_body_iterated"] = bool(iterated)
            observation["closed_count"] = len(closed)
            observation["response_messages"] = len(messages)
            observation["final_status"] = store.status(KEY_ID)
            observation["ledger"] = ledger_rows(store.path)
            observation["slots_available"] = app.state.request_slots._value
            observation["pending_after_shutdown"] = app.state.accounting.pending
        finally:
            release_provider.set()
            release_close.set()
            if not request_task.done():
                request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            if shutdown_task is None:
                await lifespan.__aexit__(None, None, None)
            else:
                await asyncio.wait_for(asyncio.shield(shutdown_task), 2)

    asyncio.run(run())
    (tmp_path / "billing-shutdown.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["shutdown_waited_for_late_response"], observation
    assert observation["request_pending_before_provider_release"], observation
    assert observation["lock_held_before_provider_release"], observation
    assert observation["shutdown_waited_for_late_close"], observation
    assert observation["lock_held_before_close_release"], observation
    assert observation["billing_capacity_retained_during_close"] == 1, observation
    assert observation["request_cancelled"] and observation["lock_released_after_shutdown"], observation
    assert observation["provider_calls"] == observation["closed_count"] == 1
    assert not observation["late_body_iterated"] and observation["response_messages"] == 0
    assert observation["slots_available"] == 10 and observation["pending_after_shutdown"] == 0
    assert observation["final_status"]["active_requests"] == 0
    assert observation["final_status"]["tokens_uncertain"] == prepare(PAYLOAD, settings.model)[1]
    assert len(observation["ledger"]) == 1 and observation["ledger"][0]["state"] == "unknown"


def test_sse_timeout_ends_before_billing_writer_releases(tmp_path):
    settings = settings_for(tmp_path, timeout=0.05)
    payload = {**PAYLOAD, "stream": True}
    observation = {}

    async def run():
        started = None
        stream_entered = asyncio.Event()
        stream_closed = asyncio.Event()
        calls = []
        closed = []
        messages = []
        writer = None

        class WaitingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal started
                writer.start()
                started = time.monotonic()
                stream_entered.set()
                yield b'data: {"type":"response.output_text.delta","delta":"offline"}\n\n'
                await asyncio.Event().wait()

            async def aclose(self):
                closed.append(True)
                stream_closed.set()

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            calls.append(str(request.url))
            return httpx.Response(200, stream=WaitingStream())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            writer = HeldWriter(store.path)
            receive, send = asgi_exchange(payload, messages)
            request_task = asyncio.create_task(app(request_scope(credential), receive, send))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                try:
                    await asyncio.wait_for(stream_entered.wait(), 1)
                    await asyncio.sleep(0.025)
                    health = await client.get("/healthz")
                    observation["health_status"] = health.status_code
                    observation["health_seconds"] = time.monotonic() - started
                    await asyncio.wait_for(asyncio.shield(request_task), 1.5)
                    observation["request_end_seconds"] = time.monotonic() - started
                    observation["writer_held_at_request_end"] = not writer.finished.is_set()
                    observation["slots_at_request_end"] = app.state.request_slots._value
                    await asyncio.wait_for(stream_closed.wait(), 0.25)
                    observation["writer_held_when_response_closed"] = not writer.finished.is_set()
                    await asyncio.to_thread(writer.join)
                    await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
                    observation["final_status"] = store.status(KEY_ID)
                    observation["ledger"] = ledger_rows(store.path)
                finally:
                    if not request_task.done():
                        request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
                    writer.release.set()
                    await asyncio.to_thread(writer.join)
        observation["provider_calls"] = len(calls)
        observation["closed_count"] = len(closed)
        observation["http_statuses"] = [m["status"] for m in messages if m["type"] == "http.response.start"]
        body = b"".join(m.get("body", b"") for m in messages)
        observation["stream_error_sent"] = b'"code": "provider_stream_failed"' in body
        observation["sse_done_sent"] = b"[DONE]" in body

    asyncio.run(run())
    (tmp_path / "billing-sse-timeout.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["request_end_seconds"] < 0.25, observation
    assert observation["health_status"] == 200 and observation["health_seconds"] < 0.25, observation
    assert observation["writer_held_at_request_end"] and observation["writer_held_when_response_closed"], observation
    assert observation["slots_at_request_end"] == 10
    assert observation["provider_calls"] == observation["closed_count"] == 1
    assert observation["http_statuses"] == [200]
    assert observation["stream_error_sent"] and not observation["sse_done_sent"]
    assert observation["final_status"]["active_requests"] == 0
    assert observation["final_status"]["tokens_uncertain"] == prepare(payload, settings.model)[1]
    assert len(observation["ledger"]) == 1 and observation["ledger"][0]["state"] == "unknown"


def test_shutdown_cancels_reserve_waiter_but_keeps_lock_through_late_refund(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, timeout=3)
    observation = {}

    async def run():
        provider_calls = []

        async def forbidden_provider(request):
            provider_calls.append(str(request.url))
            raise AssertionError("Shutdown before admission must not invoke the provider")

        app = create_app(settings, transport=httpx.MockTransport(forbidden_provider))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        store = app.state.store
        credential = app.state.vault.credential(KEY_ID)
        store.issue(KEY_ID, credential)
        reserve_entered = threading.Event()
        reserve = store.reserve

        def tracked_reserve(key_id, amount, **kwargs):
            reserve_entered.set()
            return reserve(key_id, amount, **kwargs)

        monkeypatch.setattr(store, "reserve", tracked_reserve)
        writer = HeldWriter(store.path)
        writer.start()
        messages = []
        receive, send = asgi_exchange(PAYLOAD, messages)
        request_task = asyncio.create_task(app(request_scope(credential), receive, send))
        shutdown_task = None
        try:
            assert await asyncio.to_thread(reserve_entered.wait, 1), "Real reserve worker did not start"
            started = time.monotonic()
            # Deliberately do not cancel request_task. Lifespan shutdown owns
            # cancellation while the reserve worker continues to hold its ID.
            shutdown_task = asyncio.create_task(lifespan.__aexit__(None, None, None))
            done, _ = await asyncio.wait((request_task,), timeout=0.25)
            observation["request_end_seconds"] = time.monotonic() - started
            observation["request_cancelled_by_shutdown"] = bool(done) and request_task.cancelled()
            await asyncio.sleep(0.05)
            observation["writer_held_before_release"] = not writer.finished.is_set()
            observation["shutdown_waited_for_reserve"] = not shutdown_task.done()
            observation["gateway_lock_held_before_release"] = not lock_available(settings.state_dir / "gateway.lock")
            observation["slots_before_writer_release"] = app.state.request_slots._value
            observation["provider_calls_before_release"] = len(provider_calls)
            writer.release.set()
            await asyncio.to_thread(writer.join)
            await asyncio.wait_for(asyncio.shield(shutdown_task), 2)
            await asyncio.gather(request_task, return_exceptions=True)
            observation["gateway_lock_released_after_shutdown"] = lock_available(settings.state_dir / "gateway.lock")
            observation["final_status"] = store.status(KEY_ID)
            observation["ledger"] = ledger_rows(store.path)
            observation["provider_calls"] = len(provider_calls)
            observation["pending_after_shutdown"] = app.state.accounting.pending
            observation["alive_executor_workers_after_shutdown"] = sum(
                worker.is_alive() for worker in app.state.accounting._executor._threads
            )
        finally:
            writer.release.set()
            await asyncio.to_thread(writer.join)
            if not request_task.done():
                request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            if shutdown_task is None:
                await lifespan.__aexit__(None, None, None)
            else:
                await asyncio.wait_for(asyncio.shield(shutdown_task), 2)

    asyncio.run(run())
    (tmp_path / "billing-shutdown-reserve.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["request_cancelled_by_shutdown"] and observation["request_end_seconds"] < 0.25, observation
    assert observation["writer_held_before_release"] and observation["shutdown_waited_for_reserve"], observation
    assert observation["gateway_lock_held_before_release"] and observation["gateway_lock_released_after_shutdown"], observation
    assert observation["slots_before_writer_release"] == 10
    assert observation["provider_calls_before_release"] == observation["provider_calls"] == 0
    assert observation["pending_after_shutdown"] == observation["alive_executor_workers_after_shutdown"] == 0
    final = observation["final_status"]
    assert final["active_requests"] == final["tokens_used"] == final["global_tpm_reserved"] == 0
    assert len(observation["ledger"]) == 1
    assert observation["ledger"][0]["state"] == "settled" and observation["ledger"][0]["charged"] == 0
