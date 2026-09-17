"""Real SQLite contention must leave gateway cancellation and ASGI responsive.

These tests use only the public gateway and Store behavior.  All provider
traffic is intercepted by MockTransport; the durable state is a local fixture.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from argus.trial import gateway, gateway_observation
from argus.trial import store as store_module
from argus.trial.gateway import Settings, create_app, prepare
from argus.trial.secrets import Vault, write_private

KEY_ID = "a" * 64
PAYLOAD = {
    "model": "argus-trial",
    "messages": [{"role": "user", "content": "offline billing contention fixture"}],
    "max_tokens": 100,
}
LOCK_SECONDS = 0.8
PROBE_SECONDS = 0.05
RESPONSIVENESS_SECONDS = 0.25


class HeldWriter:
    """A separate connection and OS thread hold a real SQLite writer lock."""

    def __init__(self, path):
        self.path = path
        self.held = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.errors = []
        self.thread = None

    def start(self):
        assert self.thread is None

        def hold():
            try:
                with sqlite3.connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.held.set()
                    self.release.wait(LOCK_SECONDS)
            except BaseException as exc:
                self.errors.append(repr(exc))
            finally:
                self.finished.set()

        self.thread = threading.Thread(target=hold, name="billing-fixture-writer", daemon=True)
        self.thread.start()
        assert self.held.wait(2), self.errors or "SQLite writer did not acquire its lock"

    def join(self):
        if self.thread is not None:
            self.thread.join(2)
            assert not self.thread.is_alive(), "SQLite fixture writer did not finish"
        assert not self.errors


def response_data():
    return {
        "id": "offline-billing-response", "object": "response", "created_at": 1,
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "offline result"},
        ]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def request_scope(credential):
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
        "query_string": b"", "root_path": "", "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "headers": [(b"authorization", ("Bearer " + credential).encode()),
                    (b"content-type", b"application/json")],
    }


def ledger_rows(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM trial_requests ORDER BY id")]


@pytest.mark.parametrize("phase", ["reserve", "json-settle", "sse-settle"])
def test_billing_writer_does_not_delay_asgi_or_cancellation(tmp_path, phase):
    state = tmp_path / "state"
    state.mkdir()
    key_file = tmp_path / "key"
    write_private(key_file, Fernet.generate_key())
    Vault(key_file, state / "github-token.enc").save("offline-fixture-only")
    settings = Settings(state, key_file, timeout=3)
    payload = {**PAYLOAD, "stream": phase == "sse-settle"}
    provider_calls = []
    closed = []
    observation = {"phase": phase, "lock_seconds": LOCK_SECONDS, "probe_seconds": PROBE_SECONDS}

    async def run():
        request_task = None
        health_task = None
        timer = None
        writer = None
        started = None
        tick = asyncio.Event()
        messages = []

        async def health_probe(client):
            response = await client.get("/healthz")
            observation["health_seconds"] = time.monotonic() - started
            observation["health_status"] = response.status_code

        def arm_probe(client):
            nonlocal started, timer
            assert started is None
            started = time.monotonic()

            def probe():
                nonlocal health_task
                observation["tick_seconds"] = time.monotonic() - started
                observation["writer_held_at_tick"] = not writer.finished.is_set()
                observation["cancel_was_accepted"] = request_task.cancel()
                health_task = asyncio.create_task(health_probe(client))
                tick.set()

            timer = asyncio.get_running_loop().call_later(PROBE_SECONDS, probe)

        class CompletedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                writer.start()
                arm_probe(health_client)
                data = response_data()
                if phase == "sse-settle":
                    yield ("data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n").encode()
                else:
                    yield json.dumps(data).encode()

            async def aclose(self):
                closed.append(True)

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            provider_calls.append(str(request.url))
            if phase == "reserve":
                # On the baseline, the delayed timer only runs once the
                # synchronous reserve has already admitted this provider call.
                await asyncio.Event().wait()
            return httpx.Response(200, stream=CompletedStream())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            writer = HeldWriter(store.path)
            sent_body = False

            async def receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": json.dumps(payload).encode()}
                await asyncio.Event().wait()

            async def send(message):
                messages.append(message)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test",
            ) as health_client:
                assert (await health_client.get("/healthz")).status_code == 200
                try:
                    if phase == "reserve":
                        writer.start()
                        arm_probe(health_client)
                    request_task = asyncio.create_task(app(request_scope(credential), receive, send))
                    try:
                        await asyncio.wait_for(asyncio.shield(request_task), 4)
                        observation["request_outcome"] = "completed"
                    except asyncio.CancelledError:
                        observation["request_outcome"] = "cancelled"
                    observation["request_end_seconds"] = time.monotonic() - started
                    observation["writer_held_at_request_end"] = not writer.finished.is_set()
                    observation["status_at_request_end"] = store.status(KEY_ID)
                    await asyncio.wait_for(tick.wait(), 1)
                    await asyncio.wait_for(health_task, 1)
                    # Let the real writer finish its full hold before checking
                    # late reserve compensation and detached settlement.
                    await asyncio.to_thread(writer.join)
                    deadline = asyncio.get_running_loop().time() + 2
                    while store.status(KEY_ID)["active_requests"]:
                        assert asyncio.get_running_loop().time() < deadline, "Billing cleanup left an active reservation"
                        await asyncio.sleep(0.01)
                finally:
                    if timer is not None:
                        timer.cancel()
                    if request_task is not None and not request_task.done():
                        request_task.cancel()
                        await asyncio.gather(request_task, return_exceptions=True)
                    if health_task is not None and not health_task.done():
                        await asyncio.gather(health_task, return_exceptions=True)
                    writer.release.set()
                    await asyncio.to_thread(writer.join)

        # Lifespan exit must also drain a reserve whose worker has not yet
        # committed at the first post-lock status read.  That empty read alone
        # is not evidence that late billing work has completed.
        observation["final_status"] = store.status(KEY_ID)
        observation["ledger"] = ledger_rows(store.path)
        observation["slots_available"] = app.state.request_slots._value
        observation["provider_calls"] = len(provider_calls)
        observation["upstream_closed_count"] = len(closed)
        observation["http_statuses"] = [m["status"] for m in messages if m["type"] == "http.response.start"]
        observation["sse_done_sent"] = any(b"[DONE]" in m.get("body", b"") for m in messages)
        observation["reservation_estimate"] = prepare(payload, settings.model)[1]
        observation["source_hashes"] = {
            str(Path(module.__file__).resolve()): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (gateway, gateway_observation, store_module)
        }
        (tmp_path / "billing-responsiveness.json").write_text(json.dumps(observation, indent=2) + "\n")
        with sqlite3.connect(state / "usage.sqlite3") as source, sqlite3.connect(tmp_path / "usage-snapshot.sqlite3") as destination:
            source.backup(destination)

    asyncio.run(run())
    assert observation["tick_seconds"] < RESPONSIVENESS_SECONDS, observation
    assert observation["health_seconds"] < RESPONSIVENESS_SECONDS, observation
    assert observation["request_end_seconds"] < RESPONSIVENESS_SECONDS, observation
    assert observation["writer_held_at_tick"] and observation["writer_held_at_request_end"], observation
    assert observation["health_status"] == 200
    assert observation["slots_available"] == 10
    assert observation["provider_calls"] == (0 if phase == "reserve" else 1)
    assert observation["upstream_closed_count"] == (0 if phase == "reserve" else 1)
    final_status = observation["final_status"]
    assert final_status["active_requests"] == 0
    assert final_status["tokens_used"] == final_status["global_tpm_reserved"] == (0 if phase == "reserve" else 15)
    ledger = observation["ledger"]
    # A cancelled reserve can be prevented before commit or compensated after
    # commit.  Both outcomes are valid; neither permits a duplicate charge.
    if phase == "reserve":
        assert len(ledger) <= 1
    else:
        assert len(ledger) == 1
    assert all(row["state"] == "settled" for row in ledger)
