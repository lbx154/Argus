from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi import manager_bridge, server


@pytest.mark.parametrize("terminal", ["success", "cancelled", "error"])
def test_stream_terminal_invalidates_snapshot_cached_after_response_headers(tmp_path, monkeypatch, terminal):
    """A poll during inference must not hide commits after SSE has started."""
    sid = f"s-snapshot-{terminal}"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    memory = LifeMemory.open(life)
    item = memory.backlog.add(BacklogItem.new(title="Old scope", objective="Old scope"))
    entered, headers_sent, release = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setenv("ARGUS_WEB_SNAPSHOT_CACHE_TTL", "60")

    def forbid_provider(*_args, **_kwargs):
        raise AssertionError("Snapshot invalidation must be tested without a model")

    def manager(*_args, on_fragment, **_kwargs):
        on_fragment("phase", {"role": "manager", "label": "Waiting for the Manager decision"})
        entered.set()
        assert release.wait(5), "Test did not release the Manager request"
        # A terminal error/cancellation can follow an earlier durable mutation.
        memory.backlog.update(item.id, status="superseded")
        if terminal == "error":
            raise RuntimeError("Failure after a durable update")
        return {"kind": "chat", "reply": "Scope updated"}

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbid_provider)
    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    app = server.create_app(global_root=tmp_path)

    async def traced_app(scope, receive, send):
        async def traced_send(message):
            if scope.get("path", "").endswith("/message/stream") and message["type"] == "http.response.start":
                # This is outside the app's cache-invalidating middleware, so
                # the following GET definitely repopulates the cache later.
                headers_sent.set()
            await send(message)

        await app(scope, receive, traced_send)

    snapshot_path = f"/api/projects/{sid}/snapshot?compact=true&events_limit=1"

    def old_status(client):
        response = client.get(snapshot_path)
        assert response.status_code == 200
        return next(row["status"] for row in response.json()["backlog"] if row["id"] == item.id)

    with TestClient(traced_app) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message/stream",
                                  json={"text": "Replace the scope", "request_id": "replace"})
            try:
                assert entered.wait(3)
                assert headers_sent.wait(3)
                if terminal == "cancelled":
                    cancelled = client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "replace"})
                    assert cancelled.status_code == 200 and cancelled.json()["active"]
                assert old_status(client) == "pending"
            finally:
                release.set()
            response = pending.result(timeout=3)
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        if terminal == "error":
            assert any(frame["type"] == "error" for frame in frames)
        else:
            result = next(frame["result"] for frame in frames if frame["type"] == "done")
            assert result["kind"] == ("cancelled" if terminal == "cancelled" else "chat")
        assert next(row.status for row in LifeMemory.open(life).backlog.all() if row.id == item.id) == "superseded"
        # No extra POST or sleep may invalidate/expire the cache first.
        assert old_status(client) == "superseded"
