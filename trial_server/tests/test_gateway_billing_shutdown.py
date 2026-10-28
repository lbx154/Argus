"""Shutdown and stream timeouts retain accounting without holding HTTP open."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import portalocker
from cryptography.fernet import Fernet
from test_gateway_billing_responsiveness import (
    KEY_ID,
    PAYLOAD,
    ledger_rows,
    request_scope,
    response_data,
)

from argus_trial_server.gateway import Settings, create_app, prepare
from argus_trial_server.secrets import Vault, write_private


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
        credential = app.state.store.issue(KEY_ID)
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
