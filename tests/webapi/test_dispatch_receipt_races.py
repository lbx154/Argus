"""Dispatch receipts respect cancellation and unrelated executor control."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.daemon import life_worker as daemon_worker
from argus.webapi import manager_bridge, server
from argus.webapi.daemon_services import DaemonServices


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
    services = DaemonServices(read_status=daemon_worker.read_daemon_status,
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
        assert len(turns) == 1 and "旧目标" not in turns[0]["text"]
        assert "收到" in turns[0]["text"]
    else:
        assert turns == []


@pytest.mark.parametrize("streaming", [False, True])
def test_cancel_during_chat_status_read_suppresses_the_stale_terminal_result(
    tmp_path, monkeypatch, streaming,
):
    from argus.core.transcript import append_turn

    sid = "s-review-chat-status"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    entered, release = threading.Event(), threading.Event()
    real_read = daemon_worker.read_daemon_status
    historical_reply = "这是已经完成并保存的回复。"

    def manager(*_args, **_kwargs):
        append_turn(life, "argus", historical_reply)
        return {"kind": "chat", "reply": historical_reply}

    def read_status(root):
        entered.set()
        assert release.wait(4)
        return real_read(root)

    monkeypatch.setattr(manager_bridge, "manager_message", manager)
    monkeypatch.setattr(daemon_worker, 'read_daemon_status', read_status)
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
