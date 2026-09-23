"""A queued task and a live daemon do not prove that task execution started."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.daemon import life_worker as daemon_worker
from argus.life.memory import Backlog
from argus.manager import Manager, config_intent, dispatch, front_door
from argus.manager.domain_author import VerticalDecision
from argus.webapi import manager_state, server
from argus.webapi.daemon_services import DaemonServices

GOAL = "比较相同批量下两组延迟，并保留缺失值说明。"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("startup", ["ready", "claimed", "failed", "capacity", "died-before-start", "control-busy"])
def test_real_dispatch_ack_does_not_invent_execution_or_repeat_the_confirmation(
    tmp_path, monkeypatch, streaming, startup,
):
    sid = "s-dispatch-receipt"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life), objective=""))
    manager_state._STATES.pop(sid, None)
    manager = Manager(life, memory_maintenance_enabled=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dispatch receipt regressions must remain offline")

    def classify(_memory, _body, state, **_kwargs):
        state["_frontdoor_lifetime"] = "bounded"
        return None, None, "complex"

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_args: SimpleNamespace(manager=manager))
    monkeypatch.setattr(manager, "decide_vertical", lambda *_args, **_kwargs: VerticalDecision(
        choice="existing", vertical="research", workflow_mode="direct",
        execution_task=GOAL, research_target_level="exploratory",
    ))
    # This models the executor disappearing between Manager admission and the
    # endpoint's fresh daemon read. Its old 'queued' field must not hide failure.
    if startup == "died-before-start":
        monkeypatch.setattr(dispatch, "_daemon_status", lambda _root: (True, 77))
    if startup == "control-busy":
        @contextmanager
        def busy(*_args, **_kwargs):
            yield False
        monkeypatch.setattr("argus.daemon.commands.daemon_command_execution_lock", busy)

    observed = []

    def start(_sid, **_kwargs):
        rows = Backlog(life / "backlog.jsonl").pending()
        assert len(rows) == 1 and rows[0].objective == GOAL
        observed.append(rows[0].id)
        if startup == "claimed":
            claimed = Backlog(life / "backlog.jsonl").claim_next(owner="offline-executor")
            assert claimed is not None and claimed.id == rows[0].id
        if startup in {"failed", "died-before-start"}:
            return {"rc": 2, "alive": False, "error": "Synthetic startup failure"}
        if startup == "capacity":
            return {"rc": 3, "alive": False, "admission_required": True}
        return {"rc": 0, "alive": True, "pid": 77, "control_available": True}

    app = server.create_app(global_root=tmp_path, daemon_services=DaemonServices(
        read_status=daemon_worker.read_daemon_status, start=start,
    ))
    with TestClient(app) as client:
        suffix = "/message/stream" if streaming else "/message"
        response = client.post(f"/api/projects/{sid}{suffix}", json={"text": GOAL})
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")] if streaming else []
        result = next(row["result"] for row in frames if row["type"] == "done") if streaming else response.json()

    assert result["kind"] == "task" and len(observed) == (0 if startup == "control-busy" else 1)
    rows = Backlog(life / "backlog.jsonl").active()
    assert len(rows) == 1 and rows[0].objective == GOAL
    if observed:
        assert rows[0].id == observed[0]
    assert rows[0].status == ("running" if startup == "claimed" else "pending")
    reply = result["reply"]
    # No Engineer/model was started by this fixture, even when the queue was
    # atomically claimed. This acknowledgement must only report the handoff.
    assert "开始执行" not in reply and "work has started" not in reply
    if startup in {"failed", "died-before-start"}:
        assert "启动" in reply and ("失败" in reply or "没能" in reply)
    elif startup == "capacity":
        assert "等待" in reply and "槽位" in reply
    elif startup == "control-busy":
        assert "任务已保存" in reply and "正忙" in reply and "尚未确认启动" in reply
    else:
        assert "队列" in reply or "排队" in reply or "接手" in reply
    turns = [turn for turn in read_turns(life) if turn["role"] == "argus"]
    assert [turn["text"] for turn in turns] == [reply]
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    echoes = [event for event in events if event["type"] == "ui.argus"]
    if streaming:
        assert echoes == [], "The stream's final acknowledgement must not leave a second Activity reply"
        assert [row["text"] for row in frames if row["type"] == "delta"] == [reply]
    else:
        assert [event["text"] for event in echoes] == [reply]


@pytest.mark.parametrize("streaming", [False, True])
def test_cancel_during_startup_cannot_publish_a_late_dispatch_reply(tmp_path, monkeypatch, streaming):
    from argus.webapi import manager_bridge

    sid = "s-cancel-startup"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *_args, **_kwargs: {
        "kind": "task", "item": {"id": "committed-task", "status": "pending", "title": GOAL},
    })

    def start(*_args, **_kwargs):
        entered.set()
        assert release.wait(4)
        return {"rc": 0, "alive": True, "pid": 77}

    services = DaemonServices(read_status=daemon_worker.read_daemon_status, start=start)
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                  json={"text": GOAL, "request_id": "cancel-during-start"})
            try:
                assert entered.wait(2)
                response = client.post(f"/api/projects/{sid}/message/cancel",
                                       json={"request_id": "cancel-during-start"})
                assert response.status_code == 200 and response.json()["requested"]
            finally:
                release.set()
            reply = pending.result(timeout=2)
    assert reply.status_code == 200
    if streaming:
        frames = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
        result = next(row["result"] for row in frames if row["type"] == "done")
        assert not [row for row in frames if row["type"] == "delta"]
    else:
        result = reply.json()
    assert result["kind"] == "cancelled"
    assert not [turn for turn in read_turns(life) if turn["role"] == "argus"]
    assert not (life / "events.jsonl").exists()
