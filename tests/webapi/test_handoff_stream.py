from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from argus_skill.webapi.manager_dispatch import _dispatch_team_mission, _TurnEmitter
from argus_skill.webapi.routes.manager import _ManagerStreamingResponse


def test_dispatch_emits_real_handoff_wait_phase_and_forwards_cancellation(tmp_path, monkeypatch):
    from argus_skill.manager import dispatch, front_door

    prepared = SimpleNamespace(decision=SimpleNamespace(workflow_mode="staged"))
    monkeypatch.setattr(front_door, "prepare_manager_execution_task", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(dispatch, "resume_done_lifecycle_for_team_dispatch", lambda *args: False)
    monkeypatch.setattr(dispatch, "maybe_promote_to_continuous", lambda *args, **kwargs: True)
    check = lambda: False

    def enqueue(*args, **kwargs):
        assert kwargs["cancelled"] is check
        assert prepared.cancelled is check
        prepared.on_wait()
        return SimpleNamespace(id="task"), True, 123

    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)
    fragments = []
    emitter = _TurnEmitter(life_dir=tmp_path, turn_id="turn", fragment=lambda kind, payload: fragments.append((kind, payload)))
    _dispatch_team_mission(
        SimpleNamespace(), "调整当前研究的发表要求", {"config": {"continuous": True}},
        "task", check, emitter,
    )
    phases = [payload for kind, payload in fragments if kind == "phase"]
    assert "选择合适" in phases[0]["label"]
    assert "安全交接" in phases[-1]["label"]
    assert "尚未提交" in phases[-1]["label"]
    assert phases[-1]["role"] == "manager"


def test_disconnect_signals_worker_before_waiting_for_response_cleanup():
    cancelled = threading.Event()

    async def exercise():
        sent = asyncio.Event()

        async def content():
            yield "data: waiting\n\n"
            await asyncio.sleep(60)

        async def receive():
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                sent.set()

        response = _ManagerStreamingResponse(content(), cancel_event=cancelled, media_type="text/event-stream")
        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), timeout=2)

    asyncio.run(exercise())
    assert cancelled.is_set()


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
