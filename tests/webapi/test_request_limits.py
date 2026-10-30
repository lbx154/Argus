from __future__ import annotations

import asyncio

import httpx
import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import manager_bridge, server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for n in range(12):
        sid = f"s-pressure-{n}"
        (tmp_path / "projects" / sid).mkdir(parents=True)
        write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(tmp_path)))
    return server.create_app(global_root=tmp_path, auth_token="test-only")


def test_chunked_json_limit_does_not_trust_content_length(app, monkeypatch):
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *a, **k: pytest.fail("not admitted"))

    async def exercise():
        async def chunks():
            yield b'{"text":"'
            for _ in range(8 * 64 + 1):  # one 16 KiB chunk past the 8 MiB JSON limit
                yield b"x" * 16_384
            yield b'"}'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/projects/s-pressure-0/message", content=chunks(),
                headers={"Authorization":"Bearer test-only", "Content-Type":"application/json"})
            assert response.status_code == 413
    asyncio.run(exercise())
