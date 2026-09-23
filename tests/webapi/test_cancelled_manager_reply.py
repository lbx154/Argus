from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn, read_turns
from argus.daemon import life_worker as daemon_worker
from argus.life import answer_learning
from argus.manager import config_intent, front_door
from argus.webapi import server
from argus.webapi.daemon_services import DaemonServices
from argus.webapi.manager_state import interrupt_manager_turns


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cancel_kind", ["request", "project_generation"])
@pytest.mark.parametrize("late_result", ["reply", "failure"])
def test_cancelled_triage_cannot_publish_or_learn_a_late_reply(
    tmp_path, monkeypatch, streaming, cancel_kind, late_result,
):
    """Cancellation precedes late provider output, durable reply and learning."""
    sid = "s-late-manager-reply"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    append_turn(life, "argus", "Previously settled reply")
    entered, release = threading.Event(), threading.Event()
    stale = "STALE_MANAGER_REPLY_AFTER_CANCEL_4af9"
    current = "Current request completed"
    learned, starts = [], []

    def forbid_provider(*_args, **_kwargs):
        raise AssertionError("This cancellation regression must remain offline")

    def triage(_mem, body, state, *, on_fragment, **_kwargs):
        state["manager_runner"] = SimpleNamespace(
            _backend=object(),
        )
        if "Old request" in body and "Current request" not in body:
            on_fragment("delta", {"text": "Output before cancellation"})
            entered.set()
            assert release.wait(4), "The test did not release its blocked triage"
            on_fragment("phase", {"role": "manager", "label": stale})
            on_fragment("delta", {"text": stale})
            if late_result == "failure":
                raise RuntimeError("Old provider failed after cancellation")
            return stale
        return current

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbid_provider)
    monkeypatch.setattr(answer_learning, "enqueue_answer", lambda **row: learned.append(row))
    monkeypatch.setattr(config_intent, "_front_door_classify", lambda *_args, **_kwargs: (None, None, "simple"))
    monkeypatch.setattr(front_door, "manager_triage", triage)
    services = DaemonServices(
        read_status=daemon_worker.read_daemon_status,
        start=lambda *_args, **_kwargs: starts.append(True) or {"rc": 0},
    )
    path = f"/api/projects/{sid}/message" + ("/stream" if streaming else "")
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(client.post, path, json={"text": "Old request", "request_id": "old"})
            try:
                assert entered.wait(3), "The old request never reached triage"
                if cancel_kind == "request":
                    receipt = client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"})
                    assert receipt.status_code == 200
                    assert receipt.json()["active"] is True
                else:
                    interrupt_manager_turns(sid)
            finally:
                release.set()
            response = old.result(timeout=3)
        assert response.status_code == 200
        assert stale not in response.text
        if streaming:
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            result = next(frame["result"] for frame in frames if frame["type"] == "done")
            assert any(frame.get("text") == "Output before cancellation" for frame in frames)
        else:
            result = response.json()
        assert result["kind"] == "cancelled"
        assert [row["text"] for row in read_turns(life) if row["role"] == "argus"] == [
            "Previously settled reply",
        ]
        assert not learned
        assert not starts
        events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
        assert not any(row.get("type") == "ui.argus" for row in events)

        # A repeated cancel for the old ID must leave the replacement usable.
        assert client.post(f"/api/projects/{sid}/message/cancel", json={"request_id": "old"}).status_code == 200
        replacement = client.post(path, json={"text": "Current request", "request_id": "current"})
        assert replacement.status_code == 200
        assert current in replacement.text
        assert [row["text"] for row in read_turns(life) if row["role"] == "argus"] == [
            "Previously settled reply", current,
        ]
        assert [(row["operator_text"], row["reply"]) for row in learned] == [
            ("Current request", current),
        ]
        assert learned[0]["sid"] == sid
        assert learned[0]["root"] == tmp_path
