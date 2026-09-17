from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import manager_bridge, server
from argus.webapi.request_limits import JSON_BODY_MAX_BYTES, MESSAGE_MAX_CHARS


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for n in range(12):
        sid = f"s-pressure-{n}"
        (tmp_path / "projects" / sid).mkdir(parents=True)
        write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(tmp_path)))
    return server.create_app(global_root=tmp_path, auth_token="test-only")


@pytest.mark.parametrize("stream", [False, True])
def test_oversized_or_invalid_message_never_reaches_manager(app, monkeypatch, stream):
    calls = []
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *a, **k: calls.append(a))
    with TestClient(app) as client:
        url = "/api/projects/s-pressure-0/message" + ("/stream" if stream else "")
        headers = {"Authorization": "Bearer test-only"}
        for body, status in [
            ({"text": "x" * (MESSAGE_MAX_CHARS + 1)}, 422),
            ({"text": "x" * JSON_BODY_MAX_BYTES}, 413),
            ({"text": " \n\t"}, 400),
            ({"text": "hi", "attachments": [{"attachment_id":"x"}] * 6}, 422),
        ]:
            response = client.post(url, json=body, headers=headers)
            assert response.status_code == status
            assert len(response.content) < 2048, "Errors must not echo the pasted document"
        assert client.post(url, json={"text":"hello"}).status_code == 401
    assert not calls


def test_chunked_json_limit_does_not_trust_content_length(app, monkeypatch):
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *a, **k: pytest.fail("not admitted"))

    async def exercise():
        async def chunks():
            yield b'{"text":"'
            for _ in range(17):
                yield b"x" * 16_384
            yield b'"}'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/projects/s-pressure-0/message", content=chunks(),
                headers={"Authorization":"Bearer test-only", "Content-Type":"application/json"})
            assert response.status_code == 413
    asyncio.run(exercise())


def test_burst_rejects_duplicates_and_preserves_control_capacity(app, monkeypatch):
    started = set()
    lock = threading.Lock()
    release = threading.Event()

    def slow_manager(sid, text, *, cancelled, **kwargs):
        with lock:
            started.add(sid)
        deadline = time.monotonic() + 8
        while not release.wait(0.01):
            if cancelled() or time.monotonic() >= deadline:
                return {"kind":"cancelled", "reply":"Cancelled"}
        return {"kind":"chat", "reply":"Fixture complete"}

    monkeypatch.setattr(manager_bridge, "manager_message", slow_manager)

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"Authorization":"Bearer test-only"}) as client:
                tasks = []
                try:
                    tasks = [asyncio.create_task(client.post(f"/api/projects/s-pressure-{n}/message",
                        json={"text":"slow fixture", "request_id":f"primary-{n}"})) for n in range(8)]
                    async with asyncio.timeout(3):
                        while len(started) < 8:
                            await asyncio.sleep(0.01)
                    before = time.monotonic()
                    overflow = await asyncio.gather(*[client.post(f"/api/projects/s-pressure-{n % 12}/message",
                        json={"text":"burst", "request_id":f"burst-{n}"}) for n in range(48)])
                    assert all(r.status_code in {409, 503} for r in overflow)
                    assert all(r.headers.get("Retry-After") == "1" for r in overflow if r.status_code == 503)
                    stop = await client.post("/api/projects/s-pressure-0/message/cancel", json={"request_id":"primary-0"})
                    meta = await client.get("/api/meta")
                    assert stop.status_code == meta.status_code == 200
                    assert time.monotonic() - before < 2
                    assert len(started) == 8
                finally:
                    release.set()
                    await asyncio.gather(*tasks, return_exceptions=True)
                assert app.state.message_requests.active("s-pressure-0") == []
                response = await client.post("/api/projects/s-pressure-0/message", json={"text":"retry", "request_id":"retry"})
                assert response.status_code == 200
    asyncio.run(exercise())
