"""Actual TCP disconnects release admitted requests during upstream awaits."""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from argus_skill.trial import gateway, gateway_accounting
from tests.trial.test_gateway_billing_responsiveness import KEY_ID, PAYLOAD, response_data
from tests.trial.test_gateway_billing_shutdown import lock_available, settings_for


async def wait_until(condition, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.005)
    return True


@pytest.mark.parametrize("phase", ["authorization", "headers", "body"])
def test_actual_tcp_disconnect_releases_slot_before_upstream_gate(tmp_path, phase):
    settings = settings_for(tmp_path, timeout=3)
    observation = {"phase": phase, "real_inbound_tcp": True, "external_provider_calls": 0}

    async def run():
        baseline_tasks = set(asyncio.all_tasks())
        entered, release = asyncio.Event(), asyncio.Event()
        provider_calls, response_closes = [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                if phase == "body":
                    entered.set()
                    await release.wait()
                yield json.dumps(response_data()).encode()

            async def aclose(self):
                response_closes.append(True)

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            provider_calls.append(str(request.url))
            if phase == "headers":
                entered.set()
                await release.wait()
            return httpx.Response(200, stream=Body())

        app = gateway.create_app(settings, transport=httpx.MockTransport(provider))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.setblocking(False)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="on"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        writer = None
        try:
            assert await wait_until(lambda: server.started)
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            if phase == "authorization":
                original = app.state.copilot.authorization

                async def authorization():
                    entered.set()
                    await release.wait()
                    return await original()

                app.state.copilot.authorization = authorization
            _reader, writer = await asyncio.open_connection(*listener.getsockname())
            body = json.dumps(PAYLOAD).encode()
            headers = ("POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                       + "Authorization: Bearer " + credential + "\r\nContent-Type: application/json\r\n"
                       + f"Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n")
            writer.write(headers.encode() + body)
            await writer.drain()
            await asyncio.wait_for(entered.wait(), 2)
            observation["connections_before_abort"] = len(server.server_state.connections)
            started = time.monotonic()
            # No direct cancellation of the ASGI task: exercise real Uvicorn
            # connection_lost/http.disconnect after an actual socket abort.
            writer.transport.abort()
            await writer.wait_closed()
            assert await wait_until(lambda: not server.server_state.connections)
            observation["connection_removed_seconds"] = time.monotonic() - started
            observation["request_released_before_gate"] = await wait_until(
                lambda: app.state.request_slots._value == 10 and not app.state.accounting._requests,
                timeout=0.25,
            )
            observation["request_release_seconds"] = time.monotonic() - started
            await asyncio.sleep(max(0, 0.4 - (time.monotonic() - started)))
            observation["gate_still_closed"] = not release.is_set()
            observation["connections_while_gate_closed"] = len(server.server_state.connections)
            observation["slots_while_gate_closed"] = app.state.request_slots._value
            observation["pending_while_gate_closed"] = app.state.accounting.pending
            observation["status_while_gate_closed"] = store.status(KEY_ID)
            release.set()
            await asyncio.wait_for(app.state.accounting.wait_idle(), 2)
            assert await wait_until(lambda: not server.server_state.tasks)
            observation["final_status"] = store.status(KEY_ID)
            observation["mock_provider_calls"] = len(provider_calls)
            observation["response_close_count"] = len(response_closes)
            with store.transaction() as db:
                observation["requests"] = [dict(row) for row in db.execute("SELECT * FROM trial_requests")]
                observation["attempts"] = [dict(row) for row in db.execute("SELECT * FROM trial_gateway_attempts")]
        finally:
            release.set()
            if writer is not None:
                writer.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, 5)
            listener.close()
        await asyncio.sleep(0)
        observation["new_tasks_remaining_after_shutdown"] = [
            task.get_name() for task in asyncio.all_tasks() if task not in baseline_tasks and not task.done()
        ]
        observation["source_hashes"] = {
            str(Path(module.__file__).resolve()): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (gateway, gateway_accounting)
        }

    asyncio.run(run())
    (tmp_path / "tcp-disconnect-observation.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["connections_before_abort"] == 1 and observation["connections_while_gate_closed"] == 0
    assert observation["request_released_before_gate"] and observation["request_release_seconds"] < 0.3, observation
    assert observation["gate_still_closed"] and observation["slots_while_gate_closed"] == 10, observation
    assert observation["pending_while_gate_closed"] == observation["status_while_gate_closed"]["active_requests"] == 0
    assert observation["mock_provider_calls"] == (0 if phase == "authorization" else 1)
    assert observation["new_tasks_remaining_after_shutdown"] == []
    assert observation["final_status"]["active_requests"] == 0
    assert len(observation["requests"]) == 1
    request = observation["requests"][0]
    if phase == "authorization":
        assert request["state"] == "settled" and request["charged"] == 0
    else:
        assert request["state"] == "unknown" and request["charged"] == request["reserved"]
    assert len(observation["attempts"]) == 1
    assert observation["attempts"][0]["outcome"] == "disconnected"


def test_tcp_disconnect_then_shutdown_keeps_late_response_and_close_owned(tmp_path):
    settings = settings_for(tmp_path, timeout=3)
    observation = {"real_inbound_tcp": True, "external_provider_calls": 0}

    async def run():
        baseline_tasks = set(asyncio.all_tasks())
        entered, release_provider = asyncio.Event(), asyncio.Event()
        first_cancel, repeated_cancel = asyncio.Event(), asyncio.Event()
        close_entered, release_close = asyncio.Event(), asyncio.Event()
        calls, cancellations, iterated, close_calls, closed = [], [], [], [], []

        class LateBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                iterated.append(True)
                yield json.dumps(response_data()).encode()

            async def aclose(self):
                close_calls.append(True)
                close_entered.set()
                await release_close.wait()
                closed.append(True)

        async def provider(request):
            assert str(request.url) == "https://api.githubcopilot.com/responses"
            calls.append(str(request.url))
            entered.set()
            while True:
                try:
                    await release_provider.wait()
                    break
                except asyncio.CancelledError:
                    cancellations.append(True)
                    first_cancel.set()
                    if len(cancellations) >= 2:
                        repeated_cancel.set()
            return httpx.Response(200, stream=LateBody())

        app = gateway.create_app(settings, transport=httpx.MockTransport(provider))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.setblocking(False)
        # Exercise Uvicorn's normal bounded graceful shutdown, including its
        # subsequent cancellation of a still-running HTTP task.
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="error", access_log=False, lifespan="on", timeout_graceful_shutdown=0.05,
        ))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        writer = None
        try:
            assert await wait_until(lambda: server.started)
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            _reader, writer = await asyncio.open_connection(*listener.getsockname())
            body = json.dumps(PAYLOAD).encode()
            headers = ("POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                       + "Authorization: Bearer " + credential + "\r\nContent-Type: application/json\r\n"
                       + f"Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n")
            writer.write(headers.encode() + body)
            await writer.drain()
            await asyncio.wait_for(entered.wait(), 2)
            writer.transport.abort()
            await writer.wait_closed()
            assert await wait_until(lambda: not server.server_state.connections)
            await asyncio.wait_for(first_cancel.wait(), 1)
            server.should_exit = True
            await asyncio.wait_for(repeated_cancel.wait(), 2)
            assert await wait_until(lambda: app.state.accounting.closing)
            observation["cancel_signals_before_provider_release"] = len(cancellations)
            observation["shutdown_waited_for_late_response"] = not server_task.done()
            observation["lock_held_before_provider_release"] = not lock_available(settings.state_dir / "gateway.lock")
            release_provider.set()
            await asyncio.wait_for(close_entered.wait(), 2)
            await asyncio.sleep(0.05)
            observation["shutdown_waited_for_late_close"] = not server_task.done()
            observation["lock_held_before_close_release"] = not lock_available(settings.state_dir / "gateway.lock")
            observation["lease_retained_before_close_release"] = app.state.accounting.pending
            release_close.set()
            await asyncio.wait_for(asyncio.shield(server_task), 3)
            observation["lock_released_after_shutdown"] = lock_available(settings.state_dir / "gateway.lock")
            observation["mock_provider_calls"] = len(calls)
            observation["late_body_iterated"] = bool(iterated)
            observation["close_invocations"] = len(close_calls)
            observation["close_completions"] = len(closed)
            observation["final_status"] = store.status(KEY_ID)
            observation["pending_after_shutdown"] = app.state.accounting.pending
            observation["active_request_monitors_after_shutdown"] = len(app.state.accounting._requests)
            observation["alive_billing_workers_after_shutdown"] = sum(
                worker.is_alive() for worker in app.state.accounting._executor._threads
            )
            with store.transaction() as db:
                observation["requests"] = [dict(row) for row in db.execute("SELECT * FROM trial_requests")]
        finally:
            release_provider.set()
            release_close.set()
            if writer is not None:
                writer.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, 5)
            listener.close()
        await asyncio.sleep(0)
        observation["new_tasks_remaining_after_shutdown"] = [
            task.get_name() for task in asyncio.all_tasks() if task not in baseline_tasks and not task.done()
        ]

    asyncio.run(run())
    (tmp_path / "tcp-late-response-observation.json").write_text(json.dumps(observation, indent=2) + "\n")
    assert observation["cancel_signals_before_provider_release"] >= 2
    assert observation["shutdown_waited_for_late_response"] and observation["lock_held_before_provider_release"]
    assert observation["shutdown_waited_for_late_close"] and observation["lock_held_before_close_release"]
    assert observation["lease_retained_before_close_release"] == 1
    assert observation["lock_released_after_shutdown"] and not observation["late_body_iterated"]
    assert observation["mock_provider_calls"] == observation["close_invocations"] == observation["close_completions"] == 1
    assert observation["pending_after_shutdown"] == observation["active_request_monitors_after_shutdown"] == 0
    assert observation["alive_billing_workers_after_shutdown"] == 0
    assert observation["new_tasks_remaining_after_shutdown"] == []
    assert observation["final_status"]["active_requests"] == 0
    assert len(observation["requests"]) == 1
    request = observation["requests"][0]
    assert request["state"] == "unknown" and request["charged"] == request["reserved"]
