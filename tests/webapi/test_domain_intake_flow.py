from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import Backlog
from argus.manager import Manager, config_intent, front_door
from argus.manager.domain_author import VerticalDecision, parse_domain_proposal
from argus.manager.domain_intake import read_intake
from argus.webapi import manager_state, server
from argus.webapi.daemon_services import DaemonServices


@pytest.mark.parametrize("forced", [False, True])
def test_web_opt_in_clarification_and_dispatch_create_one_real_candidate(tmp_path, monkeypatch, forced):
    sid = "s-domain-intake"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    manager_state._STATES.pop(sid, None)
    manager = Manager(life, memory_maintenance_enabled=False)
    choices = iter([
        *([] if forced else [{"action": "offer"}]),
        {"action": "ask", "question": "What output and verification do you want?"},
        {"action": "prepare", "name": "calendar_conventions"},
    ])

    def classify(_mem, _body, state, **_kwargs):
        state["_frontdoor_domain"] = next(choices)
        state["_frontdoor_self_mode"] = "reply"
        return None, None, "simple"

    classified = []

    def decide(body, **_kwargs):
        assert forced and not classified, "Approved domain must not be classified again"
        classified.append(body)
        return VerticalDecision(choice="new", vertical="calendar_conventions", execution_task=body,
                                proposal=parse_domain_proposal({"name": "calendar_conventions"}), workflow_mode="direct")

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *_a, **_kw: None)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: SimpleNamespace(manager=manager))
    monkeypatch.setattr(manager, "decide_vertical", decide)
    started = []

    def start(*_a, **_kw):
        started.append(True)
        return {"rc": 0, "alive": True, "pid": 77, "control_available": True}

    app = server.create_app(global_root=tmp_path, daemon_services=DaemonServices(
        read_status=server.read_daemon_status, start=start,
    ))
    with TestClient(app) as client:
        url = f"/api/projects/{sid}/message"
        def send(text):
            return client.post(url, json={"text": text, "route_override": "task" if forced else "auto"}).json()

        first = send("Explain a traditional calendar date")
        assert first["kind"] == "chat" and "one agent" in first["reply"]
        assert not started and not Backlog(life / "backlog.jsonl").history()
        second = send("Yes, build a specialist workflow")
        assert second["reply"] == "What output and verification do you want?"
        assert not (life / "research/DOMAINS/calendar_conventions.json").exists()
        third = send("Cultural education, cite conventions, state uncertainty")
        assert third["kind"] == "task", third
    assert len(started) == 1
    items = Backlog(life / "backlog.jsonl").history()
    assert len(items) == 1
    assert "First fetch relevant primary references" in items[0].objective
    assert "Cultural education" in items[0].objective
    assert (life / "research/DOMAINS/calendar_conventions.json").is_file()
    assert read_intake(life)["consented"] is True
    from argus.webapi.map_view import read_map

    card = read_map(sid, tmp_path, life)["tasks"][0]
    assert card["title"].startswith("Explain a traditional calendar date")
    assert "Cultural education" in card["objective"]
    assert "provided project Skill libraries" not in card["objective"]
    assert "provided project Skill libraries" in items[0].objective
