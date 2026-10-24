"""One Manager session per project, run only by its owner; the team reports into it."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.team_reports import show_team_message
from argus.core.transcript import read_turns
from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.manager import front_door
from argus.manager._session_ops import _ManagerSession
from argus.webapi import manager_bridge, manager_state
from tests.hand_off import manager_runner

pytest.importorskip("fastapi")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    manager_state._STATES.clear()
    life = tmp_path / "projects" / "s-owner"
    life.mkdir(parents=True)
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


def _runner(monkeypatch, project: Path, seeds: list, *, thread_id: str = "", **kwargs):
    def during(turn):
        seeds.append(turn["seed_thread_id"])
        if thread_id:  # what the session wrapper does after a provider call
            _ManagerSession(None, project)._write_tid(thread_id)

    runner = manager_runner(hand=None, during=during, **kwargs)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)
    return runner


def _start_daemon(project: Path, monkeypatch) -> None:
    class _Supervisor:
        def __init__(self, *args, **kwargs) -> None:
            self.config = kwargs["config"]

        def run(self) -> dict:
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", _Supervisor)
    worker = LifeWorker(LifeWorkerConfig(life_dir=project, backend="memory", poll_interval=0.01))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]
    assert worker.run_forever() == 0


def test_every_turn_continues_the_one_session_kept_with_the_project(project: Path, monkeypatch) -> None:
    seeds: list = []
    _runner(monkeypatch, project, seeds, reply="Hello.", thread_id="thread-A")

    manager_bridge.manager_message("s-owner", "hello", global_root=project.parent.parent)
    manager_state._STATES.clear()  # the web server restarted
    manager_bridge.manager_message("s-owner", "again", global_root=project.parent.parent)

    assert seeds == [None, "thread-A"]


def test_the_team_report_continues_the_session_the_ticket_was_handed_in(project: Path, monkeypatch) -> None:
    seeds: list = []
    _runner(monkeypatch, project, seeds, reply="Handed to the team.", thread_id="thread-A")
    manager_bridge.manager_message("s-owner", "make hello.py", global_root=project.parent.parent)

    _start_daemon(project, monkeypatch)  # the ticket starts the team's daemon
    manager_bridge.manager_team_report(
        "s-owner", "Task done", message_id="mission-result-x", event_fields={}, global_root=project.parent.parent,
    )

    assert seeds == [None, "thread-A"]


def test_a_failed_turn_starts_the_next_one_fresh(project: Path, monkeypatch) -> None:
    _ManagerSession(None, project)._write_tid("thread-A")
    seeds: list = []
    _runner(monkeypatch, project, seeds, failure="provider broke")

    manager_bridge.manager_message("s-owner", "hello", global_root=project.parent.parent)

    assert seeds == ["thread-A"]
    assert front_door.manager_session(SimpleNamespace(project_root=project)) is None


def test_the_owner_answers_a_team_report_in_the_same_session(project: Path, monkeypatch) -> None:
    _ManagerSession(None, project)._write_tid("thread-A")
    seeds: list = []
    runner = _runner(monkeypatch, project, seeds, reply="Done: your file is out.txt.")

    manager_bridge.manager_team_report(
        "s-owner", "Task done; result in out.txt", message_id="mission-result-x",
        event_fields={"mission_result": True, "item_id": "x"}, global_root=project.parent.parent,
    )

    assert runner.turns == ["Team report:\nTask done; result in out.txt"]
    assert seeds == ["thread-A"]
    [turn] = [t for t in read_turns(project) if t.get("message_id") == "mission-result-x"]
    assert turn["text"] == "Done: your file is out.txt."
    assert turn["mission_result"] is True


def test_the_operator_reads_the_team_report_when_the_manager_cannot_answer(project: Path, monkeypatch) -> None:
    _runner(monkeypatch, project, [], failure="provider broke")

    manager_bridge.manager_team_report(
        "s-owner", "Task done; result in out.txt", message_id="mission-result-x",
        event_fields={}, global_root=project.parent.parent,
    )

    [turn] = [t for t in read_turns(project) if t.get("message_id") == "mission-result-x"]
    assert turn["text"] == "Task done; result in out.txt"


def test_what_the_team_said_costs_no_turn_and_goes_with_the_next_one(project: Path, monkeypatch) -> None:
    """s-ec08e4a5 (10-24): a Manager turn on each team report put the result
    in the chat 4.7 minutes late, and held the session meanwhile."""
    runner = _runner(monkeypatch, project, [], reply="Run `python out.py`.")
    show_team_message(project, text="Task done; result in out.txt", message_id="mission-result-x",
                      event_fields={"mission_result": True})
    assert runner.turns == []
    [shown] = read_turns(project)
    assert shown["text"] == "Task done; result in out.txt"

    _runner(monkeypatch, project, [], failure="provider broke")
    manager_bridge.manager_message("s-owner", "how do I run it?", global_root=project.parent.parent)
    runner = _runner(monkeypatch, project, [], reply="Run `python out.py`.")
    manager_bridge.manager_message("s-owner", "how do I run it?", global_root=project.parent.parent)
    manager_bridge.manager_message("s-owner", "thanks", global_root=project.parent.parent)

    # A failed turn leaves it for the next; a turn that ran carried it.
    told = "The team told the operator this in the chat since your last turn"
    assert f"{told} (for your context):\n\nTask done; result in out.txt" in runner.turns[0]
    assert told not in runner.turns[1]


def test_a_decision_only_the_manager_makes_is_pushed_to_its_owner_at_once(project: Path, monkeypatch) -> None:
    """The team waits on a stage ruling: the web server that owns the Manager's
    session runs the turn when the project process asks, with no inbox to poll."""
    import threading
    from contextlib import nullcontext
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient

    from argus.core import team_reports
    from argus.core.session import SessionMeta, write_session_meta
    from argus.webapi import server

    global_root = project.parent.parent
    write_session_meta(global_root, SessionMeta(id="s-owner", workdir=str(global_root)))
    ran: list = []
    done = threading.Event()
    monkeypatch.setattr(manager_bridge, "manager_team_report",
                        lambda sid, text, **kwargs: ran.append((sid, text, kwargs["message_id"])) or done.set())
    # No server has said where it is: the question waits for the next turn.
    assert not team_reports.push_to_manager(project, message_id="stage-question-1", text="Stage question")

    with TestClient(server.create_app(global_root=global_root, auth_token="secret")) as client:
        client.get("/api/projects")  # any request tells project processes where the owner is
        endpoint = global_root / "manager-endpoint.json"
        assert json.loads(endpoint.read_text()) == {"url": "http://testserver:80", "token": "secret"}
        assert endpoint.stat().st_mode & 0o777 == 0o600

        def urlopen(request, timeout):
            response = client.post(urlsplit(request.full_url).path, content=request.data,
                                   headers=dict(request.header_items()))
            return nullcontext(SimpleNamespace(status=response.status_code))

        monkeypatch.setattr(team_reports.urllib.request, "urlopen", urlopen)
        for _ in range(2):
            assert team_reports.push_to_manager(project, message_id="stage-question-1", text="Stage question")
        assert done.wait(5)
        assert client.post("/api/projects/s-owner/manager/team-turn",
                           json={"message_id": "stage-question-2", "text": "x"}).status_code == 401
    assert ran == [("s-owner", "Stage question", "stage-question-1")]
