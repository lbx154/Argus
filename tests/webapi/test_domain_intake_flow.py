from types import SimpleNamespace

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import Backlog
from argus.manager import Manager, front_door
from argus.manager.domain_intake import read_intake
from argus.webapi import server
from tests.hand_off import manager_runner


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

    manager = Manager(life, runner=Backend())
    monkeypatch.setattr(front_door, '_ensure_manager_runner', lambda *_a: manager_runner(manager))
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
