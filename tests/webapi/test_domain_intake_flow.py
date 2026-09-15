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
@pytest.mark.parametrize("cards", [False, True])
def test_web_opt_in_clarification_and_dispatch_create_one_real_candidate(tmp_path, monkeypatch, forced, cards):
    sid = "s-domain-intake"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    manager_state._STATES.pop(sid, None)
    manager = Manager(life, memory_maintenance_enabled=False)
    choices = iter([
        *([] if forced else [{"action": "offer"}]),
        *([] if cards else [{"action": "ask", "question": "What output and verification do you want?"}]),
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
        card = first["decision_card"]
        assert card["kind"] == "domain_intake" and card["item_id"] == ""
        assert [option["id"] for option in card["options"]] == ["direct", "build"]
        # The snapshot survives a process restart and includes old pending
        # intakes without manufacturing blocked backlog tasks.
        manager_state._STATES.pop(sid, None)
        snapshot = client.get(f"/api/projects/{sid}/snapshot?compact=true").json()
        assert snapshot["pending_questions"][0]["operator_decision"]["id"] == card["id"]
        assert snapshot["backlog"] == []
        def answer(card, option_id, note=""):
            return client.post(url, json={"text": note or option_id, "domain_answer": {
                "id": card["id"], "option_id": option_id, "note": note,
            }}).json()

        second = answer(card, "build") if cards else send("Yes, build a specialist workflow")
        assert second["decision_card"]["id"] != card["id"]
        assert second["decision_card"]["options"] == []
        assert "What should it produce" in second["reply"] if cards else second["reply"] == "What output and verification do you want?"
        assert not (life / "research/DOMAINS/calendar_conventions.json").exists()
        if cards:
            assert answer(card, "direct")["resolved"] is False, "An old tab must not answer the next question"
            assert answer(second["decision_card"], "custom")["resolved"] is False
        note = "Cultural education, cite conventions, state uncertainty"
        third = answer(second["decision_card"], "custom", note) if cards else send(note)
        assert third["kind"] == "task", third
        if cards:
            assert answer(second["decision_card"], "custom", note)["resolved"] is False
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


def test_direct_card_retries_failure_without_losing_question_or_repeating_success(tmp_path, monkeypatch):
    from argus.core.transcript import read_turns
    from argus.manager.domain_intake import handle_intake, intake_card
    from argus.webapi.map_view import read_map

    sid = 's-direct-card'
    life = tmp_path / 'projects' / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    manager_state._STATES.pop(sid, None)
    handle_intake(life, 'Compute 12345 times 6789', {'action': 'offer'}, route='simple',
                  self_mode='reply', known_verticals=())
    card = intake_card(read_intake(life))
    calls = []

    def unexpected(*_a, **_kw):
        raise AssertionError('A button must not call the choice classifier or start a daemon')

    def execute(_mem, text, _state, **kwargs):
        calls.append((text, kwargs))
        if len(calls) == 1:
            raise RuntimeError('temporary provider failure')
        return '83,810,205'

    monkeypatch.setattr(config_intent, '_front_door_classify', unexpected)
    monkeypatch.setattr(front_door, 'manager_triage', execute)
    app = server.create_app(global_root=tmp_path, daemon_services=DaemonServices(
        read_status=server.read_daemon_status, start=unexpected,
    ))
    payload = {'text': '/stop', 'domain_answer': {'id': card['id'], 'option_id': 'direct', 'note': 'Show the result'}}
    with TestClient(app) as client:
        url = f'/api/projects/{sid}/message'
        failed = client.post(url, json=payload).json()
        assert failed['kind'] == 'error'
        assert intake_card(read_intake(life))['id'] == card['id']
        result = client.post(url, json=payload).json()
        assert result['reply'] == '83,810,205'
        assert intake_card(read_intake(life)) is None
        assert client.post(url, json=payload).json()['resolved'] is False
    assert len(calls) == 2
    assert all('Compute 12345 times 6789' in text and 'Show the result' in text for text, _ in calls)
    assert all(kwargs['route'] == 'simple' and kwargs['root_task_id'] for _, kwargs in calls)
    assert not Backlog(life / 'backlog.jsonl').history()
    assert any(task['status'] == 'done' for task in read_map(sid, tmp_path, life)['tasks'])
    assert not any('/stop' in row['text'] for row in read_turns(life)), 'Transport text is not a command'
