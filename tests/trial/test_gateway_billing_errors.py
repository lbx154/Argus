"""A failed SSE response never waits for its detached accounting write."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from argus_skill.trial.gateway import create_app, prepare
from argus_skill.trial.store import TrialError
from tests.trial.test_gateway_billing_lifecycle import asgi_request, issue, offline_settings
from tests.trial.test_gateway_billing_responsiveness import KEY_ID, PAYLOAD, HeldWriter, ledger_rows


@pytest.mark.parametrize("failure", ["malformed", "transport", "oserror", "trial"])
def test_non_timeout_sse_error_ends_while_billing_writer_is_held(tmp_path, failure):
    settings = offline_settings(tmp_path)
    payload = {**PAYLOAD, "stream": True}
    observation = {"failure": failure}

    async def run():
        writer = None
        started = None
        calls = []
        closed = []

        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal started
                writer.start()
                started = time.monotonic()
                if failure == "malformed":
                    yield b"data: {broken\n\n"
                elif failure == "transport":
                    raise httpx.ReadError("offline stream failed")
                elif failure == "oserror":
                    raise OSError("offline socket failed")
                else:
                    raise TrialError(502, "offline_stream_failure", "Offline stream failed")

            async def aclose(self):
                closed.append(True)

        def provider(request):
            calls.append(request)
            return httpx.Response(200, stream=BrokenStream())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            credential = issue(app)
            store = app.state.store
            writer = HeldWriter(store.path)
            task, _, messages = asgi_request(app, credential, payload=payload)
            try:
                await asyncio.wait_for(task, 3)
                observation["request_end_seconds"] = time.monotonic() - started
                observation["writer_held_at_request_end"] = not writer.finished.is_set()
                observation["slots_at_request_end"] = app.state.request_slots._value
                body = b"".join(message.get("body", b"") for message in messages)
                observation["http_statuses"] = [message["status"] for message in messages if message["type"] == "http.response.start"]
                observation["error_sent"] = b'"error"' in body
                observation["done_sent"] = b"[DONE]" in body
            finally:
                writer.release.set()
                await asyncio.to_thread(writer.join)
                await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
            observation["final_status"] = store.status(KEY_ID)
            observation["ledger"] = ledger_rows(store.path)
            observation["provider_calls"] = len(calls)
            observation["closed_count"] = len(closed)

    asyncio.run(run())
    (tmp_path / "sse-error-billing.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["request_end_seconds"] < 0.25, observation
    assert observation["writer_held_at_request_end"], observation
    assert observation["http_statuses"] == [200]
    assert observation["error_sent"] and not observation["done_sent"]
    assert observation["slots_at_request_end"] == 10
    assert observation["provider_calls"] == observation["closed_count"] == 1
    assert observation["final_status"]["active_requests"] == 0
    assert observation["final_status"]["tokens_uncertain"] == prepare(payload, settings.model)[1]
    assert len(observation["ledger"]) == 1 and observation["ledger"][0]["state"] == "unknown"
