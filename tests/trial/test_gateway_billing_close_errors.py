"""Upstream close failures preserve the selected HTTP and billing outcome."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from argus.trial import gateway, gateway_accounting, gateway_observation
from argus.trial import store as store_module
from argus.trial.gateway import create_app, prepare
from tests.trial.test_gateway_billing_responsiveness import (
    KEY_ID,
    PAYLOAD,
    request_scope,
    response_data,
)
from tests.trial.test_gateway_billing_shutdown import asgi_exchange, settings_for


@pytest.mark.parametrize("mode", ["json-rejected", "json-body-close", "sse-complete"])
@pytest.mark.parametrize("error_type", [OSError, httpx.ReadError])
def test_upstream_close_errors_keep_http_and_observation_consistent(tmp_path, mode, error_type):
    settings = settings_for(tmp_path, timeout=1)
    payload = {**PAYLOAD, "stream": mode == "sse-complete"}
    observation = {"mode": mode, "close_error_type": error_type.__name__}

    async def run():
        calls = []
        closes = []
        messages = []

        class FailingCloseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                data = response_data()
                if mode == "sse-complete":
                    yield ("data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n").encode()
                else:
                    yield json.dumps(data).encode()

            async def aclose(self):
                closes.append(True)
                raise error_type("offline injected upstream close failure")

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            calls.append(str(request.url))
            return httpx.Response(400 if mode == "json-rejected" else 200, stream=FailingCloseStream())

        app = create_app(settings, transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            receive, send = asgi_exchange(payload, messages)
            observation["asgi_exception"] = None
            try:
                await app(request_scope(credential), receive, send)
            except Exception as exc:
                observation["asgi_exception"] = type(exc).__name__
            await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
            observation["final_status"] = store.status(KEY_ID)
            observation["billing_failure_count"] = app.state.accounting.failure_count
            observation["slots_available"] = app.state.request_slots._value
            observation["pending_accounting"] = app.state.accounting.pending
            with store.transaction() as db:
                observation["attempts"] = [dict(row) for row in db.execute("SELECT * FROM trial_gateway_attempts")]
                observation["ledger"] = [dict(row) for row in db.execute("SELECT * FROM trial_requests")]
        observation["provider_calls"] = len(calls)
        observation["close_invocations"] = len(closes)
        observation["http_statuses"] = [m["status"] for m in messages if m["type"] == "http.response.start"]
        body = b"".join(m.get("body", b"") for m in messages)
        observation["body_text"] = body.decode()
        observation["asgi_body_completed"] = any(
            m["type"] == "http.response.body" and not m.get("more_body", False) for m in messages
        )
        observation["sse_done_sent"] = b"[DONE]" in body
        observation["source_hashes"] = {
            str(Path(module.__file__).resolve()): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (gateway, gateway_accounting, gateway_observation, store_module)
        }

    asyncio.run(run())
    (tmp_path / "billing-close-error.json").write_text(json.dumps(observation, indent=2) + "\n")
    expected_status = {"json-rejected": 400, "json-body-close": 502, "sse-complete": 200}[mode]
    assert observation["http_statuses"] == [expected_status], observation
    assert observation["asgi_exception"] is None and observation["asgi_body_completed"], observation
    assert len(observation["attempts"]) == 1
    attempt = observation["attempts"][0]
    assert attempt["selected_response_status"] == expected_status, observation
    assert attempt["outcome"] == ("completed" if mode == "sse-complete" else "error"), observation
    assert observation["provider_calls"] == observation["close_invocations"] == 1
    assert observation["billing_failure_count"] == observation["pending_accounting"] == 0
    assert observation["slots_available"] == 10 and observation["final_status"]["active_requests"] == 0
    expected_charge = prepare(payload, settings.model)[1] if mode == "json-body-close" else 15 if mode == "sse-complete" else 0
    assert observation["final_status"]["tokens_used"] == expected_charge
    assert observation["sse_done_sent"] == (mode == "sse-complete")
    assert "offline injected" not in observation["body_text"]
