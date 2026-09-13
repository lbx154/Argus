"""Dispatch receipts respect cancellation and unrelated executor control."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.transcript import read_turns
from argus_skill.daemon.commands import daemon_command_execution_lock
from argus_skill.webapi import manager_bridge, server
from argus_skill.webapi.daemon_services import DaemonServices


def _response_result(response, streaming):
    assert response.status_code == 200
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")] if streaming else []
    result = next(row["result"] for row in frames if row["type"] == "done") if streaming else response.json()
    return result, frames


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cancel_at", ["open", "after_append"])
def test_cancel_during_receipt_io_suppresses_late_delivery(
    tmp_path, monkeypatch, streaming, cancel_at,
):
    sid = "s-review-ack-io"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *_args, **_kwargs: {
        "kind": "task", "item": {"id": "committed-task", "status": "pending", "title": "旧目标"},
    })
    entered, release = threading.Event(), threading.Event()
    real_open = Path.open

    @contextmanager
    def hold_after_close(handle):
        with handle:
            yield handle
        # The real append has completed and is visible to readers. It is a
        # historical receipt, so cancellation must preserve this record.
        entered.set()
        assert release.wait(4)

    def delayed_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path != life / "transcript.jsonl" or "a" not in mode:
            return real_open(path, *args, **kwargs)
        if cancel_at == "after_append":
            return hold_after_close(real_open(path, *args, **kwargs))
        # Cancellation wins before the file opens or any receipt is appended.
        entered.set()
        assert release.wait(4)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", delayed_open)
    services = DaemonServices(read_status=server.read_daemon_status,
                             start=lambda *_args, **_kwargs: {"rc": 0, "alive": True})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                  json={"text": "旧目标", "request_id": "old-during-ack"})
            try:
                assert entered.wait(2)
                cancelled = client.post(f"/api/projects/{sid}/message/cancel",
                                        json={"request_id": "old-during-ack"})
                assert cancelled.status_code == 200 and cancelled.json()["requested"]
            finally:
                release.set()
            response = pending.result(timeout=2)

    result, frames = _response_result(response, streaming)
    assert result["kind"] == "cancelled"
    assert not [frame for frame in frames if frame["type"] == "delta"]
    assert not (life / "events.jsonl").exists()
    turns = [turn for turn in read_turns(life) if turn["role"] == "argus"]
    if cancel_at == "after_append":
        assert len(turns) == 1 and "旧目标" in turns[0]["text"]
        assert "队列" in turns[0]["text"]
    else:
        assert turns == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status,expected", [("done", "已完成"), ("paused_operator", "已暂停")])
def test_duplicate_without_pending_work_ignores_unrelated_control_lock(
    tmp_path, monkeypatch, streaming, status, expected,
):
    sid = "s-review-replay-busy"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *_args, **_kwargs: {
        "kind": "task", "dispatch_state": "already_queued",
        "item": {"id": "existing-task", "status": status, "title": "比较两组延迟"},
    })

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A completed or operator-paused task must not restart")

    services = DaemonServices(read_status=server.read_daemon_status, start=forbidden)
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        # The actual lifecycle lock is owned by this test thread while the
        # unrelated HTTP worker handles a completed/paused task replay.
        with daemon_command_execution_lock(life) as acquired:
            assert acquired
            response = client.post(f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                   json={"text": "比较两组延迟"})

    result, frames = _response_result(response, streaming)
    assert expected in result["reply"] and "没有重复创建" in result["reply"]
    assert "尚未确认启动" not in result["reply"]
    assert "daemon" not in result
    turns = [turn for turn in read_turns(life) if turn["role"] == "argus"]
    assert [turn["text"] for turn in turns] == [result["reply"]]
    if streaming:
        assert [frame["text"] for frame in frames if frame["type"] == "delta"] == [result["reply"]]


@pytest.mark.parametrize("streaming", [False, True])
def test_cancel_during_chat_status_read_suppresses_the_stale_terminal_result(
    tmp_path, monkeypatch, streaming,
):
    from argus_skill.core.transcript import append_turn

    sid = "s-review-chat-status"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    real_read = server.read_daemon_status
    historical_reply = "这是已经完成并保存的回复。"

    def manager(*_args, **_kwargs):
        append_turn(life, "argus", historical_reply)
        return {"kind": "chat", "reply": historical_reply}

    def read_status(root):
        entered.set()
        assert release.wait(4)
        return real_read(root)

    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    monkeypatch.setattr(server, "read_daemon_status", read_status)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                                  json={"text": "旧问题", "request_id": "chat-during-status"})
            try:
                assert entered.wait(2)
                cancelled = client.post(f"/api/projects/{sid}/message/cancel",
                                        json={"request_id": "chat-during-status"})
                assert cancelled.status_code == 200 and cancelled.json()["requested"]
            finally:
                release.set()
            response = pending.result(timeout=2)

    result, frames = _response_result(response, streaming)
    assert result["kind"] == "cancelled"
    assert not [frame for frame in frames if frame["type"] == "delta"]
    assert [turn["text"] for turn in read_turns(life) if turn["role"] == "argus"] == [historical_reply]


@pytest.mark.parametrize("streaming", [False, True])
def test_natural_pause_keeps_its_own_control_confirmation(tmp_path, monkeypatch, streaming):
    from argus_skill.adapters.agent_cli_backend import AgentCliBackend
    from argus_skill.daemon.state import read_continuous_state, write_continuous_config
    from argus_skill.manager import config_intent
    from argus_skill.webapi import manager_state

    sid = f"s-review-own-pause-{streaming}"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    write_continuous_config(life, enabled=True, objective="继续比较两组延迟")
    manager_state._STATES.pop(sid, None)
    before = manager_state.manager_control_generation(sid)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A classified pause must neither call a model nor start an executor")

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setattr(config_intent, "_front_door_classify",
                        lambda *_args, **_kwargs: (None, "pause", "simple"))
    services = DaemonServices(read_status=server.read_daemon_status, start=forbidden)
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        response = client.post(f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
                               json={"text": "先暂停一下"})

    result, _frames = _response_result(response, streaming)
    assert manager_state.manager_control_generation(sid) > before
    assert result["kind"] == "control" and result["control"] == "pause"
    assert result["pause_persisted"] and "已暂停" in result["reply"]
    assert not read_continuous_state(life).enabled
    assert [turn["text"] for turn in read_turns(life) if turn["role"] == "argus"] == [result["reply"]]
