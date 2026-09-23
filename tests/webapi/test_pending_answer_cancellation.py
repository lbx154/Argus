"""A cancelled interpretation cannot promote a late answer into role authority."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.daemon import life_worker as daemon_worker
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import front_door
from argus.webapi import server
from argus.webapi.daemon_services import DaemonServices
from argus.webapi.manager_bridge import manager_message
from argus.webapi.manager_state import interrupt_manager_turns


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cancel_kind", ["request", "project_generation"])
def test_cancelled_pending_interpretation_cannot_persist_late_once_answer(
    tmp_path, monkeypatch, streaming, cancel_kind,
):
    sid = "s-pending-answer-cancel"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    memory = LifeMemory.open(life)
    item = BacklogItem.new(title="Choose a bounded scope", objective="Compare the bounded result")
    item.status = "paused_operator"
    item.pending_question = "Which comparison may proceed?"
    memory.backlog.add(item)
    before = [row.to_jsonable() for row in memory.backlog.history()]
    entered, release = threading.Event(), threading.Event()
    starts, interpretations = [], []
    old_answer = "Use the first comparison, pending one clarification."
    late_reply = "LATE_PENDING_INTERPRETATION_93f02"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Pending-answer cancellation must remain offline")

    def interpret(_memory, body, _state, **_kwargs):
        if "Fresh reading request" in body:
            return "The current reading request is independent."
        interpretations.append(body)
        assert not (life / "operator_context.jsonl").exists()
        entered.set()
        assert release.wait(4), "Test did not release the delayed interpretation"
        return json.dumps({"is_answer": True, "resolved": False, "decision": "", "reply": late_reply})

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setattr(front_door, "manager_triage", interpret)
    services = DaemonServices(
        read_status=daemon_worker.read_daemon_status,
        start=lambda *_args, **_kwargs: starts.append(True) or {"rc": 0},
    )
    path = f"/api/projects/{sid}/message" + ("/stream" if streaming else "")
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(client.post, path, json={"text": old_answer, "request_id": "old"})
            try:
                assert entered.wait(3)
                if cancel_kind == "request":
                    cancelled = client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"})
                    assert cancelled.status_code == 200 and cancelled.json()["active"] is True
                else:
                    interrupt_manager_turns(sid)
            finally:
                release.set()
            response = old.result(timeout=3)
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")] if streaming else []
        result = next(frame["result"] for frame in frames if frame["type"] == "done") if streaming else response.json()
        assert result["kind"] == "cancelled"
        assert late_reply not in response.text
        assert len(interpretations) == 1 and not starts
        assert not (life / "operator_context.jsonl").exists()
        assert not (life / "inbox.jsonl").exists()
        assert [row.to_jsonable() for row in memory.backlog.history()] == before
        turns = read_turns(life)
        assert [row["text"] for row in turns if row["role"] == "operator"] == [old_answer]
        assert not [row for row in turns if row["role"] == "argus"]

        # A duplicate old cancel leaves the next explicit Chat usable and never
        # reinterprets that reading question as an answer to the paused task.
        assert client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"}).status_code == 200
        fresh = client.post(path, json={"text": "Fresh reading request", "request_id": "fresh", "route_override": "chat"})
        assert fresh.status_code == 200 and "The current reading request is independent." in fresh.text
        assert len(interpretations) == 1 and not starts
        assert not (life / "operator_context.jsonl").exists()
        assert [row.to_jsonable() for row in memory.backlog.history()] == before


def test_first_consumed_cancel_signal_remains_a_terminal_pending_result(tmp_path, monkeypatch):
    sid = "s-consumed-pending-cancel"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    memory = LifeMemory.open(life)
    item = BacklogItem.new(title="Pending", objective="Keep the bounded scope")
    item.status = "paused_operator"
    item.pending_question = "Proceed?"
    memory.backlog.add(item)
    armed = False
    consumed = []

    def cancelled():
        if armed and not consumed:
            consumed.append(True)
            return True
        return False

    def interpret(*_args, **_kwargs):
        nonlocal armed
        armed = True
        return json.dumps({"is_answer": True, "resolved": False, "decision": "", "reply": "Late interpretation"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A cancelled pending turn cannot continue to another model")

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setattr(front_door, "manager_triage", interpret)
    monkeypatch.setattr("argus.manager.config_intent._front_door_classify", forbidden)
    result = manager_message(sid, "Yes, within that scope.", global_root=tmp_path, cancelled=cancelled)
    assert result["kind"] == "cancelled" and consumed == [True]
    assert not (life / "operator_context.jsonl").exists()
    assert not [row for row in read_turns(life) if row["role"] == "argus"]
