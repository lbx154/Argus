"""Messages typed while a Manager chat reply is still running get answered."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.manager import config_intent, front_door
from argus.webapi import manager_bridge, manager_followups, manager_state, server


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return predicate()


def test_message_sent_during_a_running_chat_turn_is_answered_afterwards(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    sid = "s-followup"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    manager_state._STATES.pop(sid, None)
    seen = []

    def classify(mem, body, state, **kwargs):
        state.update(manager_runner_workdir=str(workspace))
        return None, None, "simple"

    def triage(mem, body, state, **kwargs):
        seen.append(body)
        return f"answer #{len(seen)}"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)
    client = TestClient(server.create_app(global_root=tmp_path))

    def conversation():
        events = client.get(f"/api/projects/{sid}/events").json()["events"]
        return [(e["type"], e["text"]) for e in events if e["type"] in {"ui.operator", "ui.argus"}]

    running_turn = manager_state._lock_for(sid)
    running_turn.acquire()  # a Manager chat reply is in progress
    try:
        first = client.post(f"/api/projects/{sid}/message/followup", json={"text": "also cover empty input"})
        second = client.post(f"/api/projects/{sid}/message/followup", json={"text": "and say how long it takes"})
        assert first.status_code == 200 and first.json()["queued"] is True
        assert second.status_code == 200
        time.sleep(0.3)
        # Shown at once, but not handled while the running reply holds the turn.
        assert conversation() == [
            ("ui.operator", "also cover empty input"),
            ("ui.operator", "and say how long it takes"),
        ]
        assert seen == []
    finally:
        running_turn.release()

    assert _wait_for(lambda: len([row for row in conversation() if row[0] == "ui.argus"]) == 2)
    assert conversation() == [
        ("ui.operator", "also cover empty input"),
        ("ui.operator", "and say how long it takes"),
        ("ui.argus", "answer #1"),
        ("ui.argus", "answer #2"),
    ]
    assert "also cover empty input" in seen[0] and "and say how long it takes" in seen[1]
    roles = [(t["role"], t["text"]) for t in read_turns(tmp_path / "projects" / sid)]
    assert roles == [
        ("operator", "also cover empty input"),
        ("argus", "answer #1"),
        ("operator", "and say how long it takes"),
        ("argus", "answer #2"),
    ]
    manager_state._STATES.pop(sid, None)


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
