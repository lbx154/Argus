"""Replay of s-efe9deac: the Observer's word missed the Manager, and the Manager could not pass it on.

10-26 13:28:03: the Engineer had parked a176d0d06768 at 13:27:12 with no GPU
assigned; the Observer's remark on it went to the operator as an alert, since
parking is a ``life.mission.completed`` and that read as delivered.
10-27 07:03:50: the Observer asked the Manager to have the Planner take up the
scheduling a stopped Planner left undone; the supervise form refused
``to=planner`` for a remark, so d57a69c9 went to the team and no Planner ran
until 07:33:21.
10-27 07:02:55: a done review of f9a8027c80f1, while the running CARE wrote its
own results, asked the Manager whether the review counts (89dbfec3).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from argus.core.models import RunnerResult
from argus.core.pipeline_state import write_pipeline_state
from argus.life import observer
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager import Manager, supervision
from argus.manager.directive import load_active_manager_directive
from tests.hand_off import submit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac"
RECORD = json.loads((FIXTURES / "observer_remarks.json").read_text(encoding="utf-8"))
#: Control at 07:03:50 with no 89dbfec3, as the 07:02:55 review no longer asks for it.
CONTROL = json.loads((FIXTURES / "steer_0703.json").read_text(encoding="utf-8"))["control"]


class _Model:
    """A backend whose every call hands off ``payload``."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def fork(self, event_callback=None):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls += 1
        submit(options, ("hand_off", self.payload))
        return RunnerResult(exit_code=0, call_id="replay", thread_id="observer-thread")


def test_a_remark_on_a_parked_task_reaches_the_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    parked = RECORD["parked"]
    memory = LifeMemory.open(tmp_path)
    log = JsonlEventSink(None, life_dir=memory.root)
    for event in (parked["started"], parked["parked"]):
        log.handle_event(event)
    supervisor = LifeSupervisor(
        memory=memory, runner=SimpleNamespace(reviewer_backend=_Model(parked["remark"])), sink=log,
        config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    supervisor.manager = SimpleNamespace(
        runner=SimpleNamespace(fork=lambda: None), manager_session_root=memory.root,
        bind_execution_workdir=lambda workdir: supervisor.manager,
    )

    supervisor._emit(parked["turn"])
    observer.settle(threading.Event())

    rows = [json.loads(line) for line in (Path(memory.root) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    (note,) = [row for row in rows if row["type"] == "life.observer.note"]
    assert (note["item_id"], note["to"], note.get("operator_alert")) == ("a176d0d06768", "manager", None)
    asked = supervision._PENDING.pop(str(Path(memory.root).resolve()))[2]
    assert asked["consult_reason"] == f"the Observer, who watches how the team works, says: {note['text']}"


def test_the_manager_can_hand_a_remark_to_the_planner(tmp_path, monkeypatch):
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    (tmp_path / "continuous.json").write_text(json.dumps(CONTROL["continuous"], ensure_ascii=False), encoding="utf-8")
    write_pipeline_state(tmp_path, CONTROL["pipeline"])
    (tmp_path / "active_manager_directive.json").write_text(json.dumps(CONTROL["directive"], ensure_ascii=False), encoding="utf-8")
    remark = RECORD["remark"]
    # The recorded reply also cited mission-view.json, a file the host no longer
    # writes or lists; the evidence it cites is the project's backlog.
    model = _Model({**remark["hand_off"], "to": "planner", "evidence_refs": ["backlog.jsonl"]})
    manager = Manager(tmp_path, runner=model)
    supervision.start_supervision(tmp_path)

    assert supervision.schedule_supervision(manager, tmp_path, remark["note"])
    record = supervision.supervise(manager, tmp_path, supervision._PENDING.pop(str(tmp_path.resolve()))[2])

    assert record["status"] == "applied" and (record["decision"]["handoff"], model.calls) == ("planner", 1)
    directive = load_active_manager_directive(tmp_path)
    assert directive.text == remark["hand_off"]["note"]
    assert directive.planner_revision == directive.revision != CONTROL["directive"]["planner_revision"]


def test_a_workspace_another_task_changed_during_a_review_asks_the_manager_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    supervision.start_supervision(tmp_path)
    manager = Manager(tmp_path, runner=_Model({}))

    assert supervision.schedule_supervision(manager, tmp_path, RECORD["review"]) is False
    assert str(tmp_path.resolve()) not in supervision._PENDING
