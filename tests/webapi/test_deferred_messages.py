from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.manager import config_intent, front_door
from argus.team._store import atomic_write_json, read_json
from argus.webapi import manager_state, server
from argus.webapi.deferred_messages import DeferredMessages


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


@pytest.mark.parametrize("stream", [False, True])
def test_busy_message_is_saved_and_answered_without_resending(tmp_path, monkeypatch, stream):
    sid = "s-deferred-message"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    manager_state._STATES.pop(sid, None)
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    busy = threading.Event()
    busy.set()
    answered = []

    def classify(mem, text, state, **kwargs):
        state["manager_runner_workdir"] = str(workspace)
        if busy.is_set():
            state["_frontdoor_failure"] = "provider concurrency limit reached (2 active calls); retry shortly"
        else:
            state.pop("_frontdoor_failure", None)
        return None, None, "simple"

    def triage(mem, text, state, **kwargs):
        answered.append(text)
        return "OpenForgeRL 还没有学习，接下来可以阅读原文。"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)
    life = tmp_path / "projects" / sid
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as client:
        endpoint = f"/api/projects/{sid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"text": "OpenForgeRL 你学了吗？"})
        assert response.status_code == 200
        assert "queued" in response.text
        assert list((life / "manager-deferred").glob("*.json"))
        busy.clear()
        wait_for(lambda: bool(answered))
        wait_for(lambda: any(t["text"].startswith("OpenForgeRL 还没有") for t in read_turns(life)))
        assert len(answered) == 1
        assert [t["text"] for t in read_turns(life) if t["role"] == "operator"] == ["OpenForgeRL 你学了吗？"]
        assert not any("not dispatched" in t["text"] for t in read_turns(life))
    assert not app.state.deferred_messages._workers


def test_waiting_messages_survive_restart_in_order_with_original_options(tmp_path):
    life = tmp_path / "projects" / "s-restart"
    life.mkdir(parents=True)
    first = DeferredMessages(lambda request, cancelled: {"kind": "provider_busy"}, retry_seconds=0.02)
    first.stop.set()  # emulate a server exiting after it saved the ACK
    for index, route in enumerate(["task", "chat"]):
        first.enqueue(life, {"turn_id": f"web-{index}", "text": str(index), "route_override": route})
    seen = []
    restarted = DeferredMessages(lambda request, cancelled: seen.append(request) or {"kind": "chat"})
    try:
        restarted.resume(life)
        wait_for(lambda: len(seen) == 2)
        assert [(r["text"], r["route_override"]) for r in seen] == [("0", "task"), ("1", "chat")]
    finally:
        restarted.close()


def test_interrupted_execution_is_not_blindly_replayed(tmp_path):
    life = tmp_path / "project"
    path = life / "manager-deferred" / "interrupted.json"
    atomic_write_json(path, {"status": "running", "created_at": 1,
                             "request": {"turn_id": "web-interrupted", "text": "Publish the release"}})
    seen = []
    queue = DeferredMessages(lambda request, cancelled: seen.append(request) or {"kind": "chat"})
    try:
        queue.resume(life)
        wait_for(lambda: read_json(path)["status"] == "needs_attention")
        assert not seen
        wait_for(lambda: bool(read_turns(life)))
        assert "interrupted" in read_turns(life)[-1]["text"]
    finally:
        queue.close()


def test_stop_cancels_a_waiting_message_durably(tmp_path):
    life = tmp_path / "project"
    life.mkdir()
    stopped = threading.Event()
    first = DeferredMessages(lambda request, cancelled: {"kind": "provider_busy"},
                             cancelled=lambda request: stopped.is_set())
    first.stop.set()
    first.enqueue(life, {"turn_id": "web-stop", "text": "Continue learning"})
    stopped.set()
    first.close()
    path = next((life / "manager-deferred").glob("*.json"))
    assert read_json(path)["status"] == "cancelled"
    called = []
    restarted = DeferredMessages(lambda request, cancelled: called.append(request) or {"kind": "chat"})
    try:
        restarted.resume(life)
        wait_for(lambda: not restarted._workers)
        assert not called
    finally:
        restarted.close()


def test_web_startup_recovers_waiting_message_without_duplicate_operator_turn(tmp_path, monkeypatch):
    from argus.core.transcript import append_turn

    sid = "s-startup-queue"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    manager_state._STATES.pop(sid, None)
    life = tmp_path / "projects" / sid
    append_turn(life, "operator", "Explain the results", message_id="web-startup-operator")
    atomic_write_json(life / "manager-deferred" / "saved.json", {
        "status": "waiting", "created_at": 1,
        "request": {"sid": sid, "global_root": str(tmp_path), "turn_id": "web-startup",
                    "text": "Explain the results", "route_override": "auto"},
    })

    def classify(mem, text, state, **kwargs):
        state["manager_runner_workdir"] = str(workspace)
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: "Recovered reply")
    with TestClient(server.create_app(global_root=tmp_path)):
        wait_for(lambda: any(t["text"] == "Recovered reply" for t in read_turns(life)))
    assert len([t for t in read_turns(life) if t["role"] == "operator"]) == 1
