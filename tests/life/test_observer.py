"""The Observer watches the team work and speaks up only when something is off.

It wakes only when the other roles have done enough since its last look, reads
the record with read-only tools, says nothing when all is well, and delivers a
remark as one event on a channel its hearer already reads. A look that fails is
skipped and never holds up the work.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.operator_context import build_operator_context_block
from argus.life import observer
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.observer import remarks
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

QUIET = "Nothing to add: the planner waits on a live job and the engineer's checks ran."
SPIN = "→ planner: you woke 14 times in 70 min on the same running job (events.jsonl ts 1760000000)"


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
        return RunnerResult(exit_code=0, agent_messages=[str(reply)], thread_id=f"thread-{len(self.calls)}")


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
def team(tmp_path):
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
    return SimpleNamespace(root=Path(memory.root), feed=feed, backend=backend, supervisor=supervisor)


def _calls(root: Path, count: int, *, label: str = "engineer-r1", cost: float = 0.01) -> None:
    """``count`` model calls in the usage ledger, as another role made them."""
    now = time.time()
    with (root / "usage.jsonl").open("a", encoding="utf-8") as handle:
        for _ in range(count):
            handle.write(json.dumps({
                "call_id": uuid.uuid4().hex, "run_label": label, "provider": "codex", "model": "gpt-5",
                "started_at": now, "completed_at": now, "status": "completed",
                "pricing_status": "priced", "pricing_tier": "test", "cost_usd": cost, "cost_basis": "provider",
            }) + "\n")


def _settle(team) -> None:
    """Wait for the look under way, if any, to finish."""
    looking = observer._WATCHES[str(team.root.resolve())].looking
    if looking is not None:
        looking.join(10)


def _event(team, event: dict | None = None) -> None:
    """The team emits one event, and any look it started finishes."""
    team.supervisor._emit(event or {"type": EventType.ROUND_START, "round_index": 1})
    _settle(team)


def test_nothing_new_never_wakes_the_observer(team):
    for _ in range(5):
        _event(team)
    _calls(team.root, 9)
    _event(team)
    _event(team, {"type": EventType.LIFE_MISSION_COMPLETED, "success": False, "status": "error"})
    assert team.backend.calls == []


def test_enough_calls_wake_it_once_and_its_own_calls_do_not_count(team):
    _calls(team.root, 10)
    _event(team)
    _event(team)
    assert len(team.backend.calls) == 1
    call = team.backend.calls[0]
    assert call["label"] == "observer" and call["options"].sandbox_mode == "read-only"
    assert call["options"].model == "reviewer-model"
    assert "10 model calls" in call["prompt"]
    _calls(team.root, 30, label="observer")
    _calls(team.root, 9)
    _event(team)
    assert len(team.backend.calls) == 1
    _calls(team.root, 1)
    _event(team)
    assert len(team.backend.calls) == 2


def test_enough_spend_wakes_it(team, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_OBSERVER_MODEL", "observer-model")
    _calls(team.root, 1, cost=2.5)
    _event(team)
    assert len(team.backend.calls) == 1
    assert team.backend.calls[0]["options"].model == "observer-model"
    assert "$2.50" in team.backend.calls[0]["prompt"]


def test_a_delivery_after_any_new_call_wakes_it(team):
    done = {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "t", "success": True, "status": "done"}
    _event(team, done)
    assert team.backend.calls == []
    _calls(team.root, 1)
    _event(team, done)
    assert len(team.backend.calls) == 1
    assert "a result was just delivered" in team.backend.calls[0]["prompt"]


def test_the_prompt_says_where_to_look_and_where_it_stopped(team):
    _calls(team.root, 10)
    _event(team)
    _calls(team.root, 10)
    _event(team)
    first, second = (call["prompt"] for call in team.backend.calls)
    assert f"`{team.root}`" in first
    assert all(name in first for name in ("events.jsonl", "agent_io.jsonl", "usage.jsonl"))
    assert "This is your first look" in first
    assert "You last looked at" in second and "This is your first look" not in second


def test_all_well_leaves_no_event_and_no_message(team):
    _calls(team.root, 10)
    _event(team)
    assert len(team.backend.calls) == 1
    assert team.feed.notes() == []
    assert "From the Observer" not in build_operator_context_block("engineer", team.root, consume_once=False)[0]


@pytest.mark.parametrize("role", ["engineer", "planner", "reviewer"])
def test_a_remark_reaches_that_role_s_next_prompt_marked_as_the_observer_s(team, role):
    text = "you said the tests pass but no test command ran (agent_io.jsonl call_id c-7)"
    team.backend.replies = [f"One thing.\n→ {role}: {text}"]
    _calls(team.root, 10)
    _event(team)
    (note,) = team.feed.notes()
    assert (note["to"], note["text"], note["agent_layer"]) == (role, text, "observer")
    block = build_operator_context_block(role, team.root, consume_once=False)[0]
    assert "## From the Observer" in block and text in block
    assert "not an operator instruction" in block
    assert "Operator steering" not in block
    other = next(name for name in ("engineer", "reviewer") if name != role)
    assert text not in build_operator_context_block(other, team.root, consume_once=False)[0]


def test_a_remark_is_heard_once(team):
    team.backend.replies = ["→ engineer: rerun the failing check (agent_io.jsonl call_id c-1)"]
    _calls(team.root, 10)
    _event(team)
    assert "rerun the failing check" in build_operator_context_block("engineer", team.root)[0]
    assert "rerun the failing check" not in build_operator_context_block("engineer", team.root)[0]


def test_a_remark_to_the_operator_is_an_alert_and_to_the_manager_goes_to_its_supervision(team, monkeypatch):
    from argus.manager import supervision

    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    manager = SimpleNamespace(runner=SimpleNamespace(fork=lambda: None), manager_session_root=team.root)
    manager.bind_execution_workdir = lambda workdir: manager
    team.supervisor.manager = manager
    team.backend.replies = ["→ operator: $40 an hour on a planner that only waits (usage.jsonl)\n"
                            "→ manager: pause planning until job 7 ends (events.jsonl ts 1760000000)"]
    _calls(team.root, 10)
    _event(team)
    operator, to_manager = team.feed.notes()
    assert operator["to"] == "operator" and operator["operator_alert"] is True
    assert to_manager["to"] == "manager" and "operator_alert" not in to_manager
    asked = supervision._PENDING.pop(str(team.root.resolve()))[2]
    assert asked["consult_reason"].endswith("says: pause planning until job 7 ends (events.jsonl ts 1760000000)")
    supervision.shutdown_supervision(team.root)


def test_the_same_remark_is_not_made_twice(team):
    team.backend.replies = [SPIN, SPIN, SPIN.replace("14 times", "20 times")]
    for _ in range(3):
        _calls(team.root, 10)
        _event(team)
    assert len(team.backend.calls) == 3
    assert [note["text"] for note in team.feed.notes()] == [
        SPIN.split(": ", 1)[1], SPIN.replace("14 times", "20 times").split(": ", 1)[1],
    ]
    assert "→ planner: you woke 14 times" in team.backend.calls[1]["prompt"]


@pytest.mark.parametrize("failure", [None, RuntimeError("provider down")])
def test_a_failed_look_is_skipped_and_the_work_goes_on(team, failure):
    team.backend.replies = [failure, SPIN]
    _calls(team.root, 10)
    assert team.supervisor._emit({"type": EventType.ROUND_START, "round_index": 1}) is True
    _settle(team)
    assert team.feed.notes() == []
    _event(team)
    assert len(team.backend.calls) == 1
    _calls(team.root, 10)
    _event(team)
    assert len(team.backend.calls) == 2 and len(team.feed.notes()) == 1


def test_a_look_never_holds_up_the_team(team):
    team.backend.release.clear()
    _calls(team.root, 10)
    team.supervisor._emit({"type": EventType.ROUND_START, "round_index": 1})
    started = time.monotonic()
    for _ in range(20):
        _calls(team.root, 10)
        team.supervisor._emit({"type": EventType.ROUND_START, "round_index": 2})
    assert time.monotonic() - started < 5
    assert len(team.backend.calls) <= 1
    team.backend.release.set()
    _settle(team)
    assert len(team.backend.calls) == 1


def test_one_observer_per_project_however_many_supervisors_run_its_missions(team, tmp_path):
    helper_backend = _Backend()
    helper = LifeSupervisor(
        memory=team.supervisor.memory, runner=SimpleNamespace(reviewer_backend=helper_backend),
        sink=team.feed, config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    team.backend.release.clear()
    _calls(team.root, 10)
    team.supervisor._emit({"type": EventType.ROUND_START, "round_index": 1})
    _calls(team.root, 10)
    helper._emit({"type": EventType.ROUND_START, "round_index": 1})
    team.backend.release.set()
    _settle(team)
    assert len(team.backend.calls) == 1 and helper_backend.calls == []


def test_it_reads_on_in_the_same_session(team):
    team.backend.backend = "codex"
    _calls(team.root, 10)
    _event(team)
    _calls(team.root, 10)
    _event(team)
    first, second = team.backend.calls
    assert first["resume"] is None and second["resume"] == "thread-1"
    assert "## Where the record is" in first["prompt"]
    assert "## Where the record is" not in second["prompt"] and "`Where the record is`" in second["prompt"]
    assert "## This look" in second["prompt"]


def test_remarks_are_read_from_baton_lines_only():
    reply = (
        "The planner spins.\n"
        "- → **planner**: wait on job 7 instead (events.jsonl)\n"
        "→ 执行：先跑一遍测试\n"
        "→ wait: job 7\n"
        "→ nobody: nothing\n"
        "→ manager:\n"
        "see → reviewer: inline arrows are prose\n"
    )
    assert remarks(reply) == [("planner", "wait on job 7 instead (events.jsonl)"), ("engineer", "先跑一遍测试")]
