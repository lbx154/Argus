"""The Observer glances at how the work flows and speaks up only when it does not.

Each time another role ends a turn it reads the task's events with the
Engineer's tools, resuming the task's session and reading only what is new,
says nothing when the flow is fine, and otherwise gives the Manager one remark
on that task through its supervision; with no Manager, the operator gets an
alert. A look that fails is skipped and never holds up the work.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.life import observer
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager import supervision
from tests.hand_off import submit

QUIET: dict = {}
SAID = "the Planner woke 14 times in 70 min only to keep waiting"
SPIN = {"remark": SAID, "seen_at": [1760000000]}


class _Backend:
    """A Reviewer backend whose looks reply from a script."""

    def __init__(self, *replies: object, name: str = "memory") -> None:
        self.backend = name
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.release = threading.Event()
        self.release.set()

    def fork(self, event_callback=None):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "options": options, "label": run_label, "resume": resume_thread_id})
        self.release.wait()
        reply = self.replies.pop(0) if self.replies else QUIET
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return RunnerResult(exit_code=1, fatal_error="provider unavailable")
        submit(options, ("hand_off", reply))
        return RunnerResult(exit_code=0, agent_messages=["Looked."], thread_id=f"thread-{len(self.calls)}")


class _Feed:
    """The team's event sink: every event, also written to events.jsonl."""

    def __init__(self, root: Path) -> None:
        self.events: list[dict] = []
        self.log = JsonlEventSink(None, life_dir=root, verbosity="full")

    def handle_event(self, event: dict) -> bool:
        self.events.append(event)
        return self.log.handle_event(event)

    def notes(self) -> list[dict]:
        return [event for event in self.events if event.get("type") == EventType.LIFE_OBSERVER_NOTE]


@pytest.fixture()
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    memory = LifeMemory.open(tmp_path / "life")
    feed = _Feed(memory.root)
    backend = _Backend()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(reviewer_backend=backend),
        sink=feed,
        config=LifeSupervisorConfig(project_worktree=tmp_path),
        reviewer_model="reviewer-model",
    )
    manager = SimpleNamespace(runner=SimpleNamespace(fork=lambda: None), manager_session_root=memory.root)
    manager.bind_execution_workdir = lambda workdir: manager
    supervisor.manager = manager
    yield SimpleNamespace(root=Path(memory.root), feed=feed, backend=backend, supervisor=supervisor)
    supervision._PENDING.pop(str(Path(memory.root).resolve()), None)


def _ends(team, role: str, item: str = "", via=None) -> None:
    """``role`` ends a turn on task ``item``, as every role call does."""
    (via or team.supervisor)._emit({"type": EventType.ROLE_SESSION_TURN, "role": role, "policy": "fresh",
                                    "action": "fresh", **({"item_id": item} if item else {})})


def _settle(team) -> None:
    """Wait for the look under way, if any, to finish."""
    observer.settle(threading.Event())


def _turn(team, role: str = "engineer", item: str = "m-1") -> None:
    """``role`` ends one turn; any look it starts finishes."""
    _ends(team, role, item if role != "planner" else "")
    _settle(team)


def _asked(team) -> str:
    """What the Manager's supervision was asked about, if anything."""
    pending = supervision._PENDING.get(str(team.root.resolve()))
    return pending[2].get("consult_reason", "") if pending else ""


def test_each_turn_another_role_ends_is_looked_at_once(team):
    for event in ({"type": EventType.ROUND_START, "round_index": 1}, {"type": EventType.ROUND_MAIN_COMPLETED, "round_index": 1}):
        team.supervisor._emit(event)
    assert team.backend.calls == []
    for count, role in enumerate(("engineer", "reviewer", "planner"), start=1):
        _turn(team, role)
        assert len(team.backend.calls) == count
    engineer, _, planner = (call["prompt"] for call in team.backend.calls)
    assert "- the Engineer, on task `m-1`, at " in engineer
    assert "- the Planner, planning for the whole project, at " in planner


def test_a_planner_turn_that_was_never_called_is_not_looked_at(team):
    """2026-10-20: skipped Planner calls woke the Observer, whose remarks woke
    the Manager, whose steers woke the Planner. A turn nobody took is no turn."""
    team.supervisor._emit({"type": EventType.LIFE_PLANNER_IDLE, "reason": "held", "model_call_skipped": True})
    _settle(team)
    assert team.backend.calls == []


def test_the_turn_that_ends_the_work_is_not_looked_at(team):
    """Trial 2: five looks after a task was delivered or the project done, all "the flow is fine"."""
    team.supervisor._emit({"type": EventType.ROLE_SESSION_TURN, "role": "reviewer", "policy": "fresh",
                           "action": "fresh", "item_id": "m-1", "ends_work": True})
    team.supervisor._emit({"type": EventType.ROLE_SESSION_TURN, "role": "planner", "policy": "fresh",
                           "action": "fresh", "ends_work": True})
    _settle(team)
    assert team.backend.calls == []


def test_only_the_engineer_reviewer_and_planner_wake_it(team):
    team.supervisor._emit({"type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "to": "manager", "text": "x"})
    _ends(team, "manager")
    _settle(team)
    assert team.backend.calls == []


def test_a_look_reads_the_events_with_the_engineer_s_access_at_low_effort(team, monkeypatch):
    _turn(team)
    call = team.backend.calls[0]
    options = call["options"]
    assert call["label"] == "observer" and options.full_auto and options.sandbox_mode is None
    assert not options.force_safe_mode and not options.dangerous_yolo
    assert (call["options"].model, call["options"].reasoning_effort) == ("reviewer-model", "low")
    assert f"`{team.root / 'events.jsonl'}`" in call["prompt"] and "agent_io" not in call["prompt"]
    monkeypatch.setenv("ARGUS_SKILL_OBSERVER_MODEL", "observer-model")
    _turn(team, "reviewer")
    assert team.backend.calls[1]["options"].model == "observer-model"


def test_a_task_keeps_one_session_that_reads_only_what_is_new_and_ends_with_it(team):
    """2026-10-20: every look opened a fresh session and read the task from its
    start again, paying for the same events at each turn."""
    team.backend.backend = "codex"
    _turn(team)
    first = time.time()
    _turn(team, "reviewer")
    _turn(team, "engineer", "m-2")
    team.feed.log.handle_event({"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "m-1", "status": "done"})
    _turn(team, "reviewer")
    _turn(team)

    calls = team.backend.calls
    assert [call["resume"] for call in calls] == [None, "thread-1", None, "thread-2", None]
    assert "## Where the record is" in calls[0]["prompt"]
    again = calls[1]["prompt"]
    assert again.startswith("## Another look") and "## Where the record is" not in again
    since = int(again.split("began at ts ", 1)[1].split(".", 1)[0])
    assert since <= first and "the Reviewer, on task `m-1`" in again
    # Once its task is delivered, the look after the last resumed one starts afresh.
    assert "## Where the record is" in calls[4]["prompt"]


def test_it_speaks_the_operator_s_language(team):
    _turn(team)
    team.feed.log.handle_event({"type": EventType.LIFE_MANAGER_INTENT_STARTED, "intent_id": "i-1",
                                "objective": "写一个 slugify 函数并跑测试"})
    _turn(team)
    first, second = (call["prompt"] for call in team.backend.calls)
    assert "in English, the operator's language" in first
    assert "in Chinese, the operator's language" in second
    # 2026-10-21: a remark the operator read said "Planner" in a Chinese sentence.
    assert "经理, 规划者, 工程师 and 审阅者" in second and "规划者" not in first


def test_it_never_takes_the_formal_review_for_a_duplicate(team):
    """2026-10-20: the Observer twice called the Reviewer's turn after an Engineer's
    in-turn subagent review "a duplicate review chain" to be merged away."""
    _turn(team)
    prompt = team.backend.calls[0]["prompt"]
    assert "formal review is how a task is accepted, never a duplicate" in prompt
    assert "subagent review inside its turn does not replace it" in prompt


@pytest.mark.parametrize("manager", [True, False])
def test_a_remark_on_a_delivered_task_goes_to_the_manager_and_with_none_the_operator_is_alerted(team, manager):
    """s-efe9deac, 10-26 13:28: a remark on a task parked for the Manager went to the operator."""
    team.feed.log.handle_event({"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "m-1", "status": "parked"})
    if not manager:
        team.supervisor.manager = None
    team.backend.replies = [SPIN]
    _turn(team)
    (note,) = team.feed.notes()
    assert (note["to"], note.get("operator_alert", False)) == ("manager" if manager else "operator", not manager)
    assert _asked(team).endswith(SAID) is manager


def test_the_same_remark_is_not_made_twice(team):
    again = SAID.replace("14 times", "20 times")
    team.backend.replies = [SPIN, SPIN, {**SPIN, "remark": again}]
    for _ in range(3):
        _turn(team, "planner")
    assert len(team.backend.calls) == 3
    assert [note["text"] for note in team.feed.notes()] == [SAID, again]
    assert SAID in team.backend.calls[1]["prompt"]


@pytest.mark.parametrize("failure", [None, RuntimeError("provider down")])
def test_a_failed_look_is_skipped_and_the_work_goes_on(team, failure):
    team.backend.replies = [failure, SPIN]
    assert team.supervisor._emit({"type": EventType.ROLE_SESSION_TURN, "role": "engineer", "policy": "fresh",
                                  "action": "fresh", "item_id": "m-1"}) is True
    _settle(team)
    assert team.feed.notes() == []
    _turn(team, "planner")
    assert len(team.backend.calls) == 2 and len(team.feed.notes()) == 1


def test_one_observer_per_project_however_many_supervisors_run_its_missions(team, tmp_path):
    helper_backend = _Backend()
    helper = LifeSupervisor(
        memory=team.supervisor.memory, runner=SimpleNamespace(reviewer_backend=helper_backend),
        sink=team.feed, config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    team.backend.release.clear()
    _ends(team, "engineer", "m-1")
    _ends(team, "reviewer", "m-2", via=helper)
    team.backend.release.set()
    _settle(team)
    assert len(team.backend.calls) == 2 and helper_backend.calls == []


def test_a_remark_says_where_it_was_seen_and_only_a_remark_does(team):
    team.backend.replies = [{"remark": SAID}, {"seen_at": [1760000000]}]
    _turn(team, "planner")
    _turn(team, "planner")
    assert team.feed.notes() == []
