import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, read_session_meta, write_session_meta
from argus.daemon import life_worker as daemon_worker
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
    calls = []
    class Backend:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            calls.append((prompt, options, run_label, resume_thread_id))
            if len(calls) == 1:
                reply = 'Decision:\nDOMAIN_ACTION=ASK\nDOMAIN_QUESTION=Explain cultural conventions or convert dates?\nOPERATOR_OPTIONS=' + json.dumps([
                    {"label": "Cultural education", "description": "Cite conventions and state uncertainty"},
                    {"label": "Date conversion", "description": "Produce a calendar comparison"},
                ])
            else:
                assert 'Cultural education' in prompt
                reply = ('Decision:\nDOMAIN_ACTION=PREPARE\nDOMAIN_NAME=calendar_conventions\n'
                         'DOMAIN_PURPOSE=Explain traditional calendar conventions for cultural education\n'
                         'VERTICAL_BRIEF=Scope: explain calendar conventions with cited cultural context.\n'
                         'Inputs: date and calendar convention; ask for the convention when absent.\n'
                         'Output: a cited explanation and uncertainty. Method: use primary calendar references.\n'
                         'Checks: a second date and missing-calendar input; exclude predictions.\n'
                         'TASK_TITLE=Build a reusable calendar workflow\n'
                         'TASK_SUMMARY=Explain calendar conventions with cited sources. Validate 2005-07-31 and another date, including missing-calendar input.')
            return SimpleNamespace(exit_code=0, thread_id='manager-conversation', last_agent_message=reply)
    manager = Manager(life, runner=Backend(), memory_maintenance_enabled=False)
    choices = iter([
        {"action": "offer"},
        *([] if cards else [{"action": "ask", "question": "What output and verification do you want?"}]),
        *([] if cards else [{"action": "ask"}]),
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
        read_status=daemon_worker.read_daemon_status, start=start,
    ))
    with TestClient(app) as client:
        url = f"/api/projects/{sid}/message"
        def send(text):
            return client.post(url, json={"text": text, "route_override": "task" if forced else "auto"}).json()

        first = send("17\nExplain a traditional calendar date: 2005-07-31")
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
        assert [o["label"] for o in second["decision_card"]["options"]] == ["Cultural education", "Date conversion"]
        assert second["reply"] == "Explain cultural conventions or convert dates?"
        assert not (life / "research/DOMAINS/calendar_conventions.json").exists()
        if cards:
            assert answer(card, "direct")["resolved"] is False, "An old tab must not answer the next question"
            assert answer(second["decision_card"], "custom")["resolved"] is False
        note = "Cultural education, cite conventions, state uncertainty"
        third = answer(second["decision_card"], "option-1", note) if cards else send(note)
        assert third["kind"] == "task", third
        if cards:
            assert answer(second["decision_card"], "custom", note)["resolved"] is False
    assert len(calls) == 2
    assert calls[0][3] is None and calls[1][3] == 'manager-conversation'
    assert all(call[1].disable_tools and call[2] == 'manager-domain-dialogue' for call in calls)
    assert len(started) == 1
    assert read_session_meta(tmp_path, sid).display_name.startswith("Build a reusable calendar")
    items = Backlog(life / "backlog.jsonl").history()
    assert len(items) == 1
    assert "First fetch relevant primary references" in items[0].objective
    assert "Cultural education" in items[0].objective
    assert (life / "research/DOMAINS/calendar_conventions.json").is_file()
    assert read_intake(life)["consented"] is True
    from argus.webapi.map_view import read_map

    card = read_map(sid, tmp_path, life)["tasks"][0]
    assert card["title"] == "Build a reusable calendar workflow"
    assert card["objective"] == "Explain calendar conventions with cited sources. Validate 2005-07-31 and another date, including missing-calendar input."
    assert "17\nExplain" not in card["objective"]
    assert "17\nExplain a traditional calendar date: 2005-07-31" in items[0].objective
    assert "Yes, build" not in card["objective"]
    assert "provided project Skill libraries" not in card["objective"]
    assert "provided project Skill libraries" in items[0].objective
    assert "different-input example" in items[0].objective
    assert "held-out inputs" in items[0].objective
    assert "rerun the counterexample and an unaffected case" in items[0].objective
    from argus.verticals import _data_domain as domains

    candidate = domains.load_data_domain("calendar_conventions", life)
    assert candidate.purpose == "Explain traditional calendar conventions for cultural education"
    assert "missing-calendar input" in candidate.role_banner("reviewer")
    # The existing verified promotion path retains the reusable contract and
    # makes the same capability available in another project.
    assert domains.promote_data_domain(life, tmp_path, "calendar_conventions", review_reason="independent verification fixture")
    next_project = tmp_path / "projects" / "s-next-use"
    assert domains.materialize_learned_data_domain(tmp_path, next_project, "calendar_conventions")
    reused = domains.load_data_domain("calendar_conventions", next_project)
    assert reused.role_banner("engineer") == candidate.role_banner("engineer")
    assert reused.role_banner("reviewer") == candidate.role_banner("reviewer")
    assert "Explain a traditional calendar date" not in reused.role_banner("engineer")


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
        read_status=daemon_worker.read_daemon_status, start=unexpected,
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


def test_bad_manager_question_keeps_offer_and_does_not_fabricate_options(tmp_path, monkeypatch):
    from argus.manager.domain_intake import handle_intake, intake_card

    sid = 's-options-failure'
    life = tmp_path / 'projects' / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    handle_intake(life, 'Interpret my calendar date', {'action': 'offer'}, route='simple', self_mode='reply', known_verticals=())
    before = read_intake(life)
    card = intake_card(before)
    calls = []

    class Backend:
        def run_exec(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(exit_code=0, thread_id='manager-failed-question',
                last_agent_message='Decision:\nDOMAIN_ACTION=ASK\nDOMAIN_QUESTION=Who is this for?')

    manager = Manager(life, runner=Backend(), memory_maintenance_enabled=False)
    monkeypatch.setattr(front_door, '_ensure_manager_runner', lambda *_a: SimpleNamespace(manager=manager))
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as client:
        url = f'/api/projects/{sid}/message'
        invalid = client.post(url, json={'text': 'forged', 'domain_answer': {'id': card['id'], 'option_id': 'not-a-choice'}}).json()
        assert invalid['resolved'] is False and calls == []
        reply = client.post(url, json={'text': 'build', 'domain_answer': {'id': card['id'], 'option_id': 'build'}}).json()
        assert reply['kind'] == 'error' and reply['resolved'] is False
    assert len(calls) == 1, 'A malformed question must not start an unbounded repair loop'
    assert read_intake(life) == before
    assert not Backlog(life / 'backlog.jsonl').history()
