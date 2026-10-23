"""Messages typed while a Manager chat reply is still running get answered."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import manager_bridge, manager_followups, manager_state, server


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return predicate()


@pytest.mark.parametrize("first_route,second_route", [
    ("task", "chat"), ("chat", "task"), ("task", "auto"),
])
def test_queued_messages_keep_their_own_route(
    tmp_path, monkeypatch, first_route, second_route,
):
    sid = "s-followup-routes"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    seen = []
    finished = threading.Event()

    def fake_message(project_id, text, **kwargs):
        seen.append((text, kwargs.get("route_override")))
        if len(seen) == 2:
            finished.set()
        return {"kind": "chat", "reply": "offline reply"}

    monkeypatch.setattr(manager_bridge, "manager_message", fake_message)
    running_turn = manager_state._lock_for(sid)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        running_turn.acquire()
        try:
            for text, route in [("first", first_route), ("second", second_route)]:
                response = client.post(
                    f"/api/projects/{sid}/message/followup",
                    json={"text": text, "route_override": route},
                )
                assert response.status_code == 200
                assert response.json()["queued"] is True
            worker = manager_followups._WORKERS[sid]
            assert seen == []
        finally:
            running_turn.release()
        assert finished.wait(5), seen
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert seen == [
        ("first", "" if first_route == "auto" else first_route),
        ("second", "" if second_route == "auto" else second_route),
    ]
