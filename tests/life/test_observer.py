"""The Observer watches the team work and speaks up only when something is off.

Each time another role ends a turn it looks at that turn's record with
read-only tools, in a fresh session, says nothing when all is well, and
delivers a remark as one event on a channel its hearer already reads, to
whoever holds the work now. A look that fails is skipped and never holds up
the work.
"""
from __future__ import annotations

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

QUIET = "Nothing to add: the engineer ran the tests it reported."
SPIN = "→ planner: you woke 14 times in 70 min on the same running job (events.jsonl ts 1760000000)"

#: The run label of each watched role's calls.
LABELS = {"engineer": "engineer-r3", "reviewer": "reviewer", "planner": "planner.cycle12"}


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


def _call(team, label: str, thread: str = "") -> str:
    """One finished model call: its frames in agent_io.jsonl, its end in events.jsonl."""
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    with (team.root / "agent_io.jsonl").open("a", encoding="utf-8") as raw:
        raw.write("".join(f'{{"call_id":"{call_id}","line":"frame {n}"}}\n' for n in range(2)))
    team.feed.log.handle_event({"type": EventType.AGENT_IO_COMPLETE, "call_id": call_id, "run_label": label,
                                "thread_id": thread or f"thread-of-{call_id}"})
    return call_id


def _ends(team, role: str, call_id: str = "", item: str = "") -> None:
    """``role`` ends the turn of that call (its thread), as every role call does."""
    thread = f"thread-of-{call_id}" if call_id else ""
    team.supervisor._emit({"type": EventType.ROLE_SESSION_TURN, "role": role, "policy": "fresh",
                           "action": "fresh", "session_id": thread, **({"item_id": item} if item else {})})


def _settle(team) -> None:
    """Wait for the look under way, if any, to finish."""
    watch = observer._WATCHES.get(str(team.root.resolve()))
    if watch is not None and watch.looking is not None:
        watch.looking.join(10)


def _turn(team, role: str = "engineer") -> str:
    """``role`` makes one model call and ends its turn; any look it starts finishes."""
    call_id = _call(team, LABELS[role])
    _ends(team, role, call_id)
    _settle(team)
    return call_id


def test_each_turn_another_role_ends_is_looked_at_once(team):
    for event in ({"type": EventType.ROUND_START, "round_index": 1}, {"type": EventType.ROUND_MAIN_COMPLETED, "round_index": 1}):
        team.supervisor._emit(event)
    assert team.backend.calls == []
    for count, role in enumerate(LABELS, start=1):
        call_id = _turn(team, role)
        assert len(team.backend.calls) == count
        prompt = team.backend.calls[-1]["prompt"]
        assert f"the {role.capitalize()}: call `{call_id}`, run label `{LABELS[role]}`" in prompt


def test_the_prompt_says_where_the_turn_s_rows_are(team):
    (team.root / "agent_io.jsonl").write_text('{"call_id":"someone-else"}\n', encoding="utf-8")
    _turn(team)
    assert "its rows are within lines 2-3 of agent_io.jsonl" in team.backend.calls[0]["prompt"]


def test_it_speaks_the_operator_s_language(team):
    _turn(team)
    team.feed.log.handle_event({"type": EventType.LIFE_MANAGER_INTENT_STARTED, "intent_id": "i-1",
                                "objective": "写一个 slugify 函数并跑测试"})
    _turn(team)
    first, second = (call["prompt"] for call in team.backend.calls)
    assert "in English, the operator's language" in first
    assert "in Chinese, the operator's language" in second


def test_the_turn_is_the_call_on_its_own_thread(team):
    mine = _call(team, "engineer-r2")
    _call(team, "engineer-r5")  # a parallel mission's Engineer, finished later
    _ends(team, "engineer", mine)
    _settle(team)
    assert f"call `{mine}`" in team.backend.calls[0]["prompt"]


def test_its_own_turns_and_the_manager_s_never_wake_it(team):
    _call(team, "observer")
    team.supervisor._emit({"type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "to": "planner", "text": "x"})
    _ends(team, "manager", _call(team, "manager-supervision"))
    _ends(team, "engineer")  # a turn with no call recorded
    _settle(team)
    assert team.backend.calls == []


def test_a_look_reads_the_turn_with_read_only_tools_and_the_reviewer_s_model(team, monkeypatch):
    _turn(team)
    call = team.backend.calls[0]
    assert call["label"] == "observer" and call["options"].sandbox_mode == "read-only"
    assert call["options"].model == "reviewer-model"
    assert f"`{team.root}`" in call["prompt"]
    assert all(name in call["prompt"] for name in ("events.jsonl", "agent_io.jsonl", "usage.jsonl"))
    monkeypatch.setenv("ARGUS_SKILL_OBSERVER_MODEL", "observer-model")
    _turn(team, "reviewer")
    assert team.backend.calls[1]["options"].model == "observer-model"


def test_all_well_leaves_no_event_and_no_message(team):
    _turn(team)
    assert len(team.backend.calls) == 1
    assert team.feed.notes() == []
    assert "From the Observer" not in build_operator_context_block("engineer", team.root, consume_once=False)[0]


@pytest.mark.parametrize("role", ["engineer", "planner", "reviewer"])
def test_a_remark_reaches_that_role_s_next_prompt_marked_as_the_observer_s(team, role):
    text = "you said the tests pass but no test command ran (agent_io.jsonl call_id c-7)"
    team.backend.replies = [f"One thing.\n→ {role}: {text}"]
    _turn(team)
    (note,) = team.feed.notes()
    assert (note["to"], note["text"], note["agent_layer"]) == (role, text, "observer")
    block = build_operator_context_block(role, team.root, consume_once=False)[0]
    assert "## From the Observer" in block and text in block
    assert "not an operator instruction" in block
    assert "Operator steering" not in block
    other = next(name for name in ("engineer", "reviewer") if name != role)
    assert text not in build_operator_context_block(other, team.root, consume_once=False)[0]


@pytest.mark.parametrize(("since", "hearer"), [
    ((), "engineer"),
    ((EventType.ROUND_REVIEW_STARTED,), "reviewer"),
    ((EventType.ROUND_REVIEW_STARTED, EventType.ROUND_START), "engineer"),
])
def test_a_remark_goes_to_whoever_holds_the_work_now(team, since, hearer):
    text = "the handoff claims 15 tests; the run shows 9 (agent_io.jsonl line 1252)"
    team.backend.replies = [f"→ engineer: {text}"]
    team.backend.release.clear()
    team.feed.log.handle_event({"type": EventType.ROUND_START, "item_id": "m-1"})
    team.feed.log.handle_event({"type": EventType.ROUND_REVIEW_STARTED, "item_id": "m-2"})
    _ends(team, "engineer", _call(team, "engineer-r1"), item="m-1")
    for kind in since:
        team.feed.log.handle_event({"type": kind, "item_id": "m-1"})
    team.backend.release.set()
    _settle(team)
    (note,) = team.feed.notes()
    assert note["to"] == hearer
    for role in ("engineer", "reviewer"):
        assert (text in build_operator_context_block(role, team.root, consume_once=False)[0]) is (role == hearer)


def test_a_remark_is_heard_once(team):
    team.backend.replies = ["→ engineer: rerun the failing check (agent_io.jsonl call_id c-1)"]
    _turn(team)
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
    _turn(team, "planner")
    operator, to_manager = team.feed.notes()
    assert operator["to"] == "operator" and operator["operator_alert"] is True
    assert to_manager["to"] == "manager" and "operator_alert" not in to_manager
    asked = supervision._PENDING.pop(str(team.root.resolve()))[2]
    assert asked["consult_reason"].endswith("says: pause planning until job 7 ends (events.jsonl ts 1760000000)")
    supervision.shutdown_supervision(team.root)


def test_the_same_remark_is_not_made_twice(team):
    team.backend.replies = [SPIN, SPIN, SPIN.replace("14 times", "20 times")]
    for _ in range(3):
        _turn(team, "planner")
    assert len(team.backend.calls) == 3
    assert [note["text"] for note in team.feed.notes()] == [
        SPIN.split(": ", 1)[1], SPIN.replace("14 times", "20 times").split(": ", 1)[1],
    ]
    assert "→ planner: you woke 14 times" in team.backend.calls[1]["prompt"]


@pytest.mark.parametrize("failure", [None, RuntimeError("provider down")])
def test_a_failed_look_is_skipped_and_the_work_goes_on(team, failure):
    team.backend.replies = [failure, SPIN]
    call_id = _call(team, "engineer-r3")
    assert team.supervisor._emit({"type": EventType.ROLE_SESSION_TURN, "role": "engineer", "policy": "fresh",
                                  "action": "fresh", "session_id": f"thread-of-{call_id}"}) is True
    _settle(team)
    assert team.feed.notes() == []
    _turn(team, "planner")
    assert len(team.backend.calls) == 2 and len(team.feed.notes()) == 1


def test_turns_that_end_during_a_look_wait_for_the_next_one_without_holding_up_the_team(team):
    team.backend.release.clear()
    _ends(team, "engineer", _call(team, "engineer-r3"))
    started = time.monotonic()
    later = [_call(team, "reviewer"), _call(team, "planner.cycle12")]
    _ends(team, "reviewer", later[0])
    _ends(team, "planner", later[1])
    assert time.monotonic() - started < 5
    assert len(team.backend.calls) <= 1
    team.backend.release.set()
    _settle(team)
    assert len(team.backend.calls) == 2
    assert all(call_id in team.backend.calls[1]["prompt"] for call_id in later)


def test_one_observer_per_project_however_many_supervisors_run_its_missions(team, tmp_path):
    helper_backend = _Backend()
    helper = LifeSupervisor(
        memory=team.supervisor.memory, runner=SimpleNamespace(reviewer_backend=helper_backend),
        sink=team.feed, config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    team.backend.release.clear()
    _ends(team, "engineer", _call(team, "engineer-r3"))
    reviewer = _call(team, "reviewer")
    helper._emit({"type": EventType.ROLE_SESSION_TURN, "role": "reviewer", "policy": "fresh", "action": "fresh",
                  "session_id": f"thread-of-{reviewer}"})
    team.backend.release.set()
    _settle(team)
    assert len(team.backend.calls) == 2 and helper_backend.calls == []


def test_each_look_is_a_fresh_session(team):
    team.backend.backend = "codex"
    _turn(team)
    _turn(team, "reviewer")
    assert [call["resume"] for call in team.backend.calls] == [None, None]
    assert all("## Where the record is" in call["prompt"] for call in team.backend.calls)
    assert not (team.root / "role-sessions").exists()


def test_the_process_waits_for_the_last_look_unless_stopped(team):
    team.backend.release.clear()
    _ends(team, "engineer", _call(team, "engineer-r1"))
    stop = threading.Event()
    stop.set()
    observer.settle(stop)
    assert observer._WATCHES[str(team.root.resolve())].looking.is_alive()
    threading.Timer(0.2, team.backend.release.set).start()
    observer.settle(threading.Event())
    assert observer._WATCHES[str(team.root.resolve())].looking is None
    assert len(team.backend.calls) == 1


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
