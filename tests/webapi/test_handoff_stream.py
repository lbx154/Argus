from __future__ import annotations

import asyncio
import threading

import pytest

from argus.webapi.routes.manager import _ManagerStreamingResponse


def test_send_failure_also_signals_worker_cancellation():
    cancelled = threading.Event()

    async def exercise():
        async def content():
            yield "data: waiting\n\n"

        async def receive():
            await asyncio.sleep(60)

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        response = _ManagerStreamingResponse(content(), cancel_event=cancelled, media_type="text/event-stream")
        with pytest.raises(Exception):
            await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send), timeout=2)

    asyncio.run(exercise())
    assert cancelled.is_set()
