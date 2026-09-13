"""Billing ownership across HTTP termination and durable recovery boundaries."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.fernet import Fernet

from argus_skill.trial.gateway import Settings, create_app, prepare
from argus_skill.trial.secrets import Vault, write_private
from argus_skill.trial.store import Store, TrialError
from tests.trial.test_gateway_billing_responsiveness import (
    KEY_ID,
    PAYLOAD,
    HeldWriter,
    ledger_rows,
    request_scope,
    response_data,
)


def offline_settings(tmp_path, *, timeout=2):
    state = tmp_path / "state"
    state.mkdir()
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("offline-fixture-only")
    return Settings(state, key, timeout=timeout)


def issue(app):
    credential = app.state.vault.credential(KEY_ID)
    app.state.store.issue(KEY_ID, credential)
    return credential


def asgi_request(app, credential, *, payload=PAYLOAD):
    sent = False
    disconnected = asyncio.Event()
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": json.dumps(payload).encode()}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    return asyncio.create_task(app(request_scope(credential), receive, send)), disconnected, messages


@pytest.mark.parametrize("phase", ["reserve", "committed-reserve", "submit"])
@pytest.mark.parametrize("termination", ["cancel", "disconnect", "timeout"])
def test_late_billing_never_dispatches_after_http_termination(tmp_path, monkeypatch, phase, termination):
    settings = offline_settings(tmp_path, timeout=0.12 if termination == "timeout" else 2)
    calls = []

    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=response_data())

    async def run():
        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            store = app.state.store
            writer = HeldWriter(store.path)
            entered = asyncio.Event()
            release_result = threading.Event()
            loop = asyncio.get_running_loop()
            reserve, submit = store.reserve, store.submit_operation

            def held_reserve(*args, **kwargs):
                if phase == "reserve":
                    writer.start()
                    loop.call_soon_threadsafe(entered.set)
                identifier = reserve(*args, **kwargs)
                if phase == "committed-reserve":
                    loop.call_soon_threadsafe(entered.set)
                    assert release_result.wait(3)
                return identifier

            def held_submit(*args, **kwargs):
                writer.start()
                loop.call_soon_threadsafe(entered.set)
                return submit(*args, **kwargs)

            monkeypatch.setattr(store, "reserve", held_reserve)
            if phase == "submit":
                monkeypatch.setattr(store, "submit_operation", held_submit)
            task, disconnected, messages = asgi_request(app, credential)
            try:
                await asyncio.wait_for(entered.wait(), 1)
                started = time.monotonic()
                if termination == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 0.3)
                else:
                    if termination == "disconnect":
                        disconnected.set()
                    await asyncio.wait_for(task, 0.3)
                    statuses = [message["status"] for message in messages if message["type"] == "http.response.start"]
                    assert statuses == ([499] if termination == "disconnect" else [502 if phase == "submit" else 429])
                assert time.monotonic() - started < 0.3
                assert app.state.request_slots._value == 10
                assert app.state.accounting.pending == 1
                assert not calls
                assert phase == "committed-reserve" or not writer.finished.is_set()
            finally:
                release_result.set()
                writer.release.set()
                await asyncio.to_thread(writer.join)
                await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
            assert not calls
            status = store.status(KEY_ID)
            assert status["active_requests"] == 0
            assert status["tokens_used"] == status["global_tpm_reserved"] == 0
            rows = ledger_rows(store.path)
            assert len(rows) == 1 and rows[0]["state"] == "settled" and rows[0]["charged"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["reserve-before", "reserve-after", "submit-after"])
def test_sqlite_error_reconciles_commit_even_when_return_value_is_lost(tmp_path, monkeypatch, phase):
    settings = offline_settings(tmp_path)
    calls = []

    async def run():
        app = create_app(settings, transport=httpx.MockTransport(lambda request: calls.append(request)))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            store = app.state.store
            original = store.submit_operation if phase == "submit-after" else store.reserve

            def failed(*args, **kwargs):
                if phase != "reserve-before":
                    original(*args, **kwargs)
                raise sqlite3.OperationalError("offline injected write failure")

            monkeypatch.setattr(store, "submit_operation" if phase == "submit-after" else "reserve", failed)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                response = await client.post("/v1/chat/completions", json=PAYLOAD,
                                             headers={"Authorization": "Bearer " + credential})
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "billing_unavailable"
                assert "injected" not in response.text
            await app.state.accounting.wait_idle()
            assert not calls
            assert app.state.accounting.failure_count == 0
            status = store.status(KEY_ID)
            assert status["tokens_used"] == status["global_tpm_reserved"] == status["active_requests"] == 0
            assert len(ledger_rows(store.path)) == (0 if phase == "reserve-before" else 1)

    asyncio.run(run())


@pytest.mark.parametrize("termination", ["timeout", "disconnect"])
def test_json_settlement_deadline_and_disconnect_keep_known_usage(tmp_path, termination):
    settings = offline_settings(tmp_path, timeout=0.08 if termination == "timeout" else 2)

    async def run():
        entered = asyncio.Event()
        writer = None
        calls = []

        class CompletedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                writer.start()
                entered.set()
                yield json.dumps(response_data()).encode()

        async def provider(request):
            calls.append(request)
            return httpx.Response(200, stream=CompletedBody())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            store = app.state.store
            writer = HeldWriter(store.path)
            task, disconnected, messages = asgi_request(app, credential)
            try:
                await asyncio.wait_for(entered.wait(), 1)
                started = time.monotonic()
                if termination == "disconnect":
                    disconnected.set()
                await asyncio.wait_for(task, 0.3)
                assert time.monotonic() - started < 0.3
                assert not writer.finished.is_set()
                assert app.state.request_slots._value == 10
                assert app.state.accounting.pending == 1
                statuses = [message["status"] for message in messages if message["type"] == "http.response.start"]
                assert statuses == [503 if termination == "timeout" else 499]
            finally:
                writer.release.set()
                await asyncio.to_thread(writer.join)
                await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
            assert len(calls) == 1
            status = store.status(KEY_ID)
            assert status["tokens_used"] == status["global_tpm_reserved"] == 15
            assert status["active_requests"] == 0
            with store.transaction() as db:
                row, = db.execute("SELECT * FROM trial_gateway_attempts").fetchall()
                assert row["selected_response_status"] == statuses[0]
                assert row["outcome"] == ("error" if termination == "timeout" else "disconnected")

    asyncio.run(run())


@pytest.mark.parametrize("submitted,stream,provider_status", [(False, False, 200), (True, False, 200), (True, True, 200), (True, False, 400)])
def test_failed_cleanup_is_explicit_and_restart_recovers_conservatively(tmp_path, monkeypatch, submitted, stream, provider_status):
    settings = offline_settings(tmp_path)
    calls = []

    def provider(request):
        calls.append(request)
        if provider_status != 200:
            return httpx.Response(provider_status, json={})
        if stream:
            return httpx.Response(200, text="data: " + json.dumps({"type": "response.completed", "response": response_data()}) + "\n\n")
        return httpx.Response(200, json=response_data())

    async def run():
        app = create_app(settings, transport=httpx.MockTransport(provider))
        with pytest.raises(RuntimeError, match="durable-state recovery"):
            async with app.router.lifespan_context(app):
                credential = issue(app)
                store = app.state.store

                def failed(*args, **kwargs):
                    raise sqlite3.OperationalError("offline injected settlement failure")

                monkeypatch.setattr(store, "settle_operation", failed)
                if not submitted:
                    async def authorization():
                        raise TrialError(503, "provider_unavailable", "Offline authorization refused")
                    monkeypatch.setattr(app.state.copilot, "authorization", authorization)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                    auth = {"Authorization": "Bearer " + credential}
                    response = await client.post("/v1/chat/completions", json={**PAYLOAD, "stream": stream}, headers=auth)
                    if stream:
                        assert response.status_code == 200
                        assert "billing_unavailable" in response.text and "[DONE]" not in response.text
                    else:
                        assert response.status_code == 503
                        assert response.json()["error"]["code"] == "billing_unavailable"
                    health = await client.get("/healthz")
                    assert health.status_code == 503 and health.json()["status"] == "billing_recovery_required"
                    assert (await client.post("/v1/chat/completions", json=PAYLOAD, headers=auth)).status_code == 503
                await app.state.accounting.wait_idle()
                assert app.state.accounting.pending == 0
                assert app.state.accounting.failure_count == 1
                assert store.status(KEY_ID)["active_requests"] == 1
                with store.transaction() as db:
                    observed = db.execute("SELECT * FROM trial_gateway_attempts ORDER BY id").fetchone()
                    assert observed["selected_response_status"] == (200 if stream else 503)
                    assert observed["client_error_code"] == "billing_unavailable"
        assert len(calls) == int(submitted)
        restarted = Store(settings.state_dir / "usage.sqlite3")
        restarted.recover()
        status = restarted.status(KEY_ID)
        assert status["active_requests"] == 0
        expected = prepare({**PAYLOAD, "stream": stream}, settings.model)[1] if submitted else 0
        assert status["tokens_used"] == status["global_tpm_reserved"] == expected
        row, = ledger_rows(restarted.path)
        assert row["state"] == ("interrupted" if submitted else "settled")

    asyncio.run(run())


def test_repeated_cancelled_requests_keep_billing_work_bounded(tmp_path, monkeypatch):
    settings = offline_settings(tmp_path)
    calls = []

    async def run():
        app = create_app(settings, transport=httpx.MockTransport(lambda request: calls.append(request)))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            store = app.state.store
            release = threading.Event()
            entered = asyncio.Event()
            loop = asyncio.get_running_loop()
            original = store.reserve

            def paused(*args, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return original(*args, **kwargs)

            monkeypatch.setattr(store, "reserve", paused)
            try:
                for index in range(30):
                    task, _, _ = asgi_request(app, credential)
                    if index == 0:
                        await asyncio.wait_for(entered.wait(), 1)
                    else:
                        # Observe actual lease/acquisition ownership, never a
                        # guessed delay, before cancelling the next request.
                        async with asyncio.timeout(1):
                            if index < 10:
                                while sum(lease.inflight is not None for lease in app.state.accounting._leases) < index + 1:
                                    await asyncio.sleep(0)
                            else:
                                while not app.state.accounting._waiters:
                                    await asyncio.sleep(0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 0.3)
                    assert app.state.accounting.pending <= 10
                    assert app.state.request_slots._value == 10
                    assert len(app.state.accounting._executor._threads) == 1
                    assert app.state.accounting._executor._work_queue.qsize() <= 10
                assert app.state.accounting.pending == 10
                assert not calls
            finally:
                release.set()
                await asyncio.wait_for(app.state.accounting.wait_idle(), 3)
            status = store.status(KEY_ID)
            assert status["tokens_used"] == status["active_requests"] == 0
            assert len(ledger_rows(store.path)) == 10
            assert app.state.accounting._slots._value == 10
        assert not any(thread.is_alive() for thread in app.state.accounting._executor._threads)

    asyncio.run(run())


def test_operation_key_is_atomic_idempotent_and_cannot_alias_another_charge(tmp_path):
    now = [100.0]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0])
    store.issue("one", "credential-one")
    store.issue("two", "credential-two")
    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(lambda _: store.reserve("one", 1000, operation_key="same"), range(20)))
    assert len(set(identifiers)) == 1
    assert store.status("one")["tokens_used"] == 1000
    for key, amount in [("one", 1001), ("two", 1000)]:
        with pytest.raises(ValueError, match="another reservation"):
            store.reserve(key, amount, operation_key="same")
    store.submit_operation("same")
    store.settle_operation("same", 15)
    now[0] = 120
    store.settle_operation("same", None)
    store.settle_operation("same", 0)
    assert store.reserve("one", 1000, operation_key="same") == identifiers[0]
    with pytest.raises(TrialError, match="no longer active"):
        store.submit_operation("same")
    assert store.status("one")["tokens_used"] == 15
    with store.transaction() as db:
        assert db.execute("SELECT retain_until FROM trial_tpm_reservations").fetchone()[0] == 160
        assert db.execute("SELECT count(*) FROM trial_requests").fetchone()[0] == 1


def test_restart_refunds_only_managed_reservations_without_submit_intent(tmp_path):
    path = tmp_path / "usage.db"
    store = Store(path, clock=lambda: 100)
    store.issue("key", "credential")
    store.reserve("key", 1000, operation_key="reserved")
    store.reserve("key", 2000, operation_key="submitted")
    store.submit_operation("submitted")
    store.reserve("key", 3000)
    restarted = Store(path, clock=lambda: 200)
    restarted.recover()
    restarted.recover()
    rows = ledger_rows(path)
    assert [(row["state"], row["charged"]) for row in rows] == [
        ("settled", 0), ("interrupted", 2000), ("interrupted", 3000),
    ]
    status = restarted.status("key")
    assert status["tokens_used"] == status["global_tpm_reserved"] == 5000
    assert status["active_requests"] == 0
