from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.manager import front_door
from argus.webapi import manager_state, server
from argus.webapi.deferred_messages import DeferredMessages, _write_record
from argus.webapi.deferred_messages import _read_record as read_json
from tests.hand_off import replying


def atomic_write_json(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_record(path, record)


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


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
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    manager_state._STATES.pop(sid, None)
    life = tmp_path / "projects" / sid
    append_turn(life, "operator", "Explain the results", message_id="web-startup-operator")
    atomic_write_json(life / "manager-deferred" / "saved.json", {
        "status": "waiting", "created_at": 1,
        "request": {"sid": sid, "global_root": str(tmp_path), "turn_id": "web-startup",
                    "text": "Explain the results", "route_override": "auto"},
    })

    monkeypatch.setattr(front_door, "manager_turn", replying(lambda *args, **kwargs: "Recovered reply"))
    with TestClient(server.create_app(global_root=tmp_path)):
        wait_for(lambda: any(t["text"] == "Recovered reply" for t in read_turns(life)))
    assert len([t for t in read_turns(life) if t["role"] == "operator"]) == 1


class _FakeStop:
    """A stop event whose waits move a fake clock instead of sleeping."""

    def __init__(self, clock):
        self.clock = clock
        self.waits: list[float] = []

    def is_set(self):
        # A queue that never gives up fails here instead of hanging the run.
        return len(self.waits) >= 1000

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock[0] += seconds
        return False


def test_busy_retries_back_off_and_stop_after_three_hours(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus.webapi import deferred_messages

    life = tmp_path / "project"
    life.mkdir()
    saved = DeferredMessages(lambda request, cancelled: {"kind": "provider_busy"})
    saved.stop.set()
    saved.enqueue(life, {"turn_id": "web-busy", "text": "Explain the results"})
    clock = [1_000.0]
    monkeypatch.setattr(deferred_messages, "time",
                        SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    calls = []
    queue = DeferredMessages(lambda request, cancelled: calls.append(clock[0]) or {"kind": "provider_busy"})
    queue.stop = _FakeStop(clock)
    queue._drain(life.resolve())
    assert queue.stop.waits[:5] == [2, 4, 8, 16, 32] and max(queue.stop.waits) == 300
    assert len(calls) == 44 and calls[-1] - calls[0] >= 3 * 3600
    path = next((life / "manager-deferred").glob("*.json"))
    assert read_json(path)["status"] == "needs_attention"
    assert "3 hours" in read_turns(life)[-1]["text"]


