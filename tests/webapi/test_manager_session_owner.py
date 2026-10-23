"""One Manager session per project, run only by its owner; the team reports into it."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.team_reports import post_team_report
from argus.core.transcript import read_turns
from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.manager import front_door
from argus.manager._session_ops import _ManagerSession
from argus.webapi import manager_bridge, manager_state
from argus.webapi.deferred_messages import DeferredMessages
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


def test_a_team_report_is_left_once_for_the_owner(project: Path) -> None:
    for _ in range(2):
        post_team_report(project, message_id="mission-result-x", text="Task done; result in out.txt",
                         event_fields={"mission_result": True, "item_id": "x"})

    records = [json.loads(p.read_text()) for p in (project / "manager-deferred").glob("*.json")]
    assert len(records) == 1
    assert records[0]["status"] == "waiting"
    assert records[0]["request"] == {
        "sid": "s-owner", "global_root": str(project.parent.parent), "text": "Task done; result in out.txt",
        "turn_id": "mission-result-x", "team": {"mission_result": True, "item_id": "x"},
    }


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


def test_the_owner_drains_team_reports_as_turns(project: Path) -> None:
    post_team_report(project, message_id="mission-result-x", text="Task done", event_fields={})
    ran: list = []
    deferred = DeferredMessages(lambda request, _cancelled: ran.append(request) or {"kind": "chat"})

    deferred.resume(project)
    deferred._workers[project.resolve()].join(5)

    assert [request["team"] for request in ran] == [{}]
    [record] = [json.loads(p.read_text()) for p in (project / "manager-deferred").glob("*.json")]
    assert record["status"] == "completed"
