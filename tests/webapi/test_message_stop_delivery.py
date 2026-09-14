from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from argus.core.models import RunnerOptions, RunnerResult
from argus.core.run_gateway import run_exec
from argus.core.session import SessionMeta, write_session_meta
from argus.manager import config_intent
from argus.webapi import manager_bridge, server
from argus.webapi.daemon_services import DaemonServices


def test_snapshot_recovers_running_manager_request_without_replaying_it(tmp_path, monkeypatch):
    sid = "s-refresh-self"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def manager(*args, cancelled, **kwargs):
        calls.append(args[1])
        entered.set()
        assert release.wait(3)
        return {"kind": "cancelled", "reply": "cancelled"} if cancelled() else {
            "kind": "chat", "reply": "finished",
        }

    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message/stream",
                                  json={"text": "Fix the page", "request_id": "self-request"})
            try:
                assert entered.wait(2)
                for _ in range(2):
                    snapshot = client.get(f"/api/projects/{sid}/snapshot?compact=true")
                    assert snapshot.status_code == 200
                    assert snapshot.json()["manager_requests"] == [
                        {"request_id": "self-request", "status": "running"},
                    ]
                assert calls == ["Fix the page"], "snapshot refresh must not replay the request"
                stopped = client.post(f"/api/projects/{sid}/message/cancel",
                                      json={"request_id": "self-request"})
                assert stopped.json()["active"] is True
            finally:
                release.set()
            assert '"cancelled"' in pending.result(timeout=2).text
            assert client.get(f"/api/projects/{sid}/snapshot?compact=true").json()["manager_requests"] == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("resolved_question", [False, True])
def test_stop_between_handoff_and_http_delivery_cannot_restart_executor(
    tmp_path, monkeypatch, streaming, resolved_question,
):
    sid = "s-message-stop"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    starts, acknowledgements = [], []
    real_read = server.read_daemon_status

    def read_status(root):
        # Hold only the delivery's status read. Stop uses the actual route,
        # control generation and command receipt, with no live process to kill.
        entered.set()
        assert release.wait(4)
        return real_read(root)

    def handoff(*args, **kwargs):
        if resolved_question:
            return {"kind": "pending_question", "resolved": True}
        return {"kind": "task", "item": {"id": "item-1", "status": "pending"}}

    monkeypatch.setattr(manager_bridge, "manager_message", handoff)
    monkeypatch.setattr(server, "read_daemon_status", read_status)
    monkeypatch.setattr(server, "stop_daemon", lambda *args, **kwargs: 0)
    monkeypatch.setattr("argus.webapi.manager_pending_question.record_task_dispatch_ack",
                        lambda *args, **kwargs: acknowledgements.append(True))
    services = DaemonServices(read_status=real_read,
        start=lambda *args, **kwargs: starts.append(True) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.post, f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                  json={"text": "继续分析这两个结果"})
            try:
                assert entered.wait(2)
                stopped = client.post(f"/api/projects/{sid}/daemon/stop", json={"force": True})
                assert stopped.status_code == 200
                assert stopped.json()["command_status"] == "applied"
            finally:
                release.set()
            response = request.result(timeout=2)
    assert response.status_code == 200
    if streaming:
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        result = next(frame["result"] for frame in frames if frame["type"] == "done")
    else:
        result = response.json()
    assert starts == []
    assert acknowledgements == []
    assert result["kind"] == "cancelled"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status,should_start", [("pending", True), ("paused_operator", False), ("done", False)])
def test_replayed_message_only_starts_work_still_pending(tmp_path, monkeypatch, streaming, status, should_start):
    sid = "s-message-replay"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *args, **kwargs: {
        "kind": "task", "dispatch_state": "already_queued", "item": {"id": "item-1", "status": status},
    })
    starts = []
    services = DaemonServices(read_status=server.read_daemon_status,
        start=lambda *args, **kwargs: starts.append(True) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        response = client.post(f"/api/projects/{sid}/message" + ("/stream" if streaming else ""), json={"text": "Continue"})
    assert response.status_code == 200
    assert bool(starts) is should_start


@pytest.mark.parametrize("streaming", [False, True])
def test_message_cancel_reaches_provider_without_waiting_for_manager_lock(tmp_path, monkeypatch, streaming):
    sid = "s-cancel-provider"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, interrupted, cleanup = threading.Event(), threading.Event(), threading.Event()

    class Backend:
        def run_exec(self, *, options, **kwargs):
            entered.set()
            while not cleanup.wait(.01):
                if reason := options.external_interrupt_reason_provider():
                    interrupted.set()
                    return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason)
            raise AssertionError("Provider never received cancellation")

    def classify(*args, **kwargs):
        run_exec(Backend(), prompt="classify", options=RunnerOptions(), run_label="manager-test")
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    starts = []
    services = DaemonServices(read_status=server.read_daemon_status,
        start=lambda *args, **kwargs: starts.append(True) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                  json={"text": "Analyze these results", "request_id": "old-request"})
            try:
                assert entered.wait(2)
                cancel = client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old-request"})
                assert cancel.status_code == 200 and cancel.json()["requested"]
                assert interrupted.wait(.75)
                response = pending.result(timeout=1)
                assert response.status_code == 200 and '"cancelled"' in response.text
                assert starts == []
            finally:
                cleanup.set()


@pytest.mark.parametrize("streaming", [False, True])
def test_cancel_before_intake_and_late_cancel_do_not_execute_or_interrupt_new_message(tmp_path, monkeypatch, streaming):
    sid = "s-cancel-order"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def manager(*args, cancelled, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        assert not cancelled(), "An old request's cancel reached the new message"
        return {"kind": "chat", "reply": "new answer"}

    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    path = f"/api/projects/{sid}/message" + ("/stream" if streaming else "")
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        assert client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"}).json()["requested"]
        assert client.post(path, json={"text": "Old", "request_id": "old"}).status_code == 409
        assert calls == []
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, path, json={"text": "New", "request_id": "new"})
            try:
                assert entered.wait(1)
                assert client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"}).status_code == 200
            finally:
                release.set()
            assert "new answer" in pending.result(timeout=2).text
        assert not client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "new"}).json()["requested"]
    assert calls == [True]


def test_browser_disconnect_keeps_task_cancellable_without_replaying(tmp_path, monkeypatch):
    import asyncio

    from argus.webapi.routes.models import MessageIn

    sid = "s-detached-action"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed = []

    def manager(*args, cancelled, on_fragment, **kwargs):
        entered.set()
        on_fragment("phase", {"label": "working"})
        try:
            assert release.wait(4)
            observed.append(cancelled())
            return {"kind": "cancelled", "reply": "Stopped"} if cancelled() else {"kind": "chat", "reply": "Done"}
        finally:
            finished.set()

    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    monkeypatch.setattr(server, "_manager_stream_heartbeat_seconds", lambda: 0.01)
    app = server.create_app(global_root=tmp_path)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", "") == "/api/projects/{sid}/message/stream")

    async def disconnect():
        response = await endpoint(sid, MessageIn(text="Do local work", request_id="survives-refresh"))
        sent = asyncio.Event()

        async def receive():
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                sent.set()

        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 2)

    try:
        asyncio.run(disconnect())
        assert entered.is_set() and not finished.is_set()
        assert app.state.message_requests.active(sid) == [{"request_id": "survives-refresh", "status": "running"}]
        with TestClient(app) as client:
            response = client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "survives-refresh"})
            assert response.json()["active"] is True
    finally:
        release.set()
    assert finished.wait(2)
    assert observed == [True]
