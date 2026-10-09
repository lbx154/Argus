"""Repeated unusable Planner turns are paced, surfaced once, and never spin.

Each daemon pass is simulated with a fake clock that advances by the sleep the
supervisor asks for, so these tests measure model calls over wall-clock time
without sleeping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.life.memory import LifeMemory
from argus.life.supervisor import _planner_failure_backoff as backoff
from argus.life.supervisor._config import LifeSupervisorConfig
from argus.life.supervisor._core import LifeSupervisor
from argus.manager import Manager

DAY_SECONDS = 24 * 3600.0
# The daemon never sleeps less than its poll interval between passes.
POLL_SECONDS = 5.0


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def of(self, event_type: str, **match: Any) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if event.get("type") == event_type
            and all(event.get(key) == value for key, value in match.items())
        ]


class _Runner:
    pass


class _ScriptedPlanner:
    """Answers every Planner call with ``reply`` until told otherwise."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.planner_calls = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label.startswith("planner.cycle"), run_label
        self.planner_calls += 1
        return RunnerResult(
            exit_code=0,
            agent_messages=[self.reply],
            stdout_lines=[],
            stderr_lines=[],
            thread_id="planner-thread",
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


_UNREADABLE = "I looked around and I am not sure what to do."
_NOT_DONE_NO_TASKS = "PROJECT_DONE=false\nREASON=still thinking about the next step"
_ONE_TASK = "\n".join([
    "PROJECT_DONE=false",
    "REASON=one concrete step remains",
    "ADVANCE_TO_STAGE=delivery",
    "TASK_KEY=write-script",
    "TASK_TITLE=Write the greeting script",
    "TASK_OBJECTIVE=Write greet.py that prints hello and run it.",
    "TASK_SCOPE=bounded",
    "TASK_STAGE_CLOSING=false",
    "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
    "TASK_SKIP_STAGE_TRANSITION=false",
])


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _project_state(project: Path) -> None:
    state = project / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({
            "vertical": "software",
            "current_stage": "delivery",
            "stages": {"delivery": {"status": "in_progress"}},
        }),
        encoding="utf-8",
    )


def _supervisor(tmp_path: Path, monkeypatch, reply: str):
    project = tmp_path / "project"
    project.mkdir()
    _project_state(project)
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    planner = _ScriptedPlanner(reply)
    inbox: list[str] = []
    runner = _Runner()
    runner.manager = Manager(project_root=project, execution_workdir=project, runner=planner)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="write greet.py that prints hello, then run it",
            paper_mission=False,
            final_certification_gate=False,
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
            user_inbox=lambda: inbox.pop(0) if inbox else None,
        ),
        planner_runner=planner,
    )
    supervisor.test_inbox = inbox
    monkeypatch.setattr(supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(supervisor, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(supervisor, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_effective_final_certification_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(supervisor, "_planner_runtime_with_idle_note", lambda: "")
    clock = _Clock()
    monkeypatch.setattr(backoff, "time", clock)
    return supervisor, planner, sink, clock


def _drive(supervisor: LifeSupervisor, clock: _Clock, seconds: float) -> list[dict[str, Any]]:
    """Run daemon passes back to back, sleeping what each pass asks for."""
    deadline = clock.now + seconds
    summaries = []
    while clock.now < deadline:
        summary = supervisor.run()
        summaries.append(summary)
        supervisor._missions_started = 0
        supervisor._planning_cycles = 0
        clock.now += max(POLL_SECONDS, float(summary.get("suggested_sleep") or 0.0))
    return summaries


def test_backoff_starts_after_the_free_attempts_and_doubles_to_a_cap() -> None:
    low = [backoff.planner_failure_backoff_seconds(n, rand=lambda: 0.0) for n in range(1, 14)]
    high = [backoff.planner_failure_backoff_seconds(n, rand=lambda: 1.0) for n in range(1, 14)]

    free = backoff.PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS
    assert low[:free] == high[:free] == [0.0] * free
    base = backoff.PLANNER_FAILURE_BACKOFF_BASE_SECONDS
    cap = backoff.PLANNER_FAILURE_BACKOFF_CAP_SECONDS
    assert high[free] == base and low[free] == base / 2
    assert high[free + 1] == 2 * base
    assert all(later >= earlier for earlier, later in zip(high, high[1:]))
    assert max(high) == cap
    assert all(cap / 2 <= value <= cap for value in high[-3:])
    # Jitter spreads the wait but never below half the nominal delay.
    assert all(lo >= hi / 2 for lo, hi in zip(low, high))


def test_failure_key_ignores_counters_but_not_the_failure() -> None:
    class Verdict:
        def __init__(self, error: str) -> None:
            self.error = error

    same_a = backoff.planner_failure_key("planner_error", Verdict("repair exhausted after 1 attempt"))
    same_b = backoff.planner_failure_key("planner_error", Verdict("repair exhausted after 2 attempt"))
    other = backoff.planner_failure_key("planner_error", Verdict("backend refused the call"))

    assert same_a == same_b
    assert same_a != other


def test_unreadable_planner_replies_are_bounded_over_a_day(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)

    summaries = _drive(supervisor, clock, DAY_SECONDS)

    # A tight loop asked thousands of times a day. The paced loop makes a few
    # attempts, reports the repeat once, then waits for a change, with only a
    # rare re-probe per long hold window.
    probes = int(DAY_SECONDS // backoff.PLANNER_FAILURE_HOLD_MAX_SECONDS) + 1
    cycles = backoff.PLANNER_FAILURE_ALERT_AFTER + probes
    assert planner.planner_calls <= 2 * cycles
    assert len(summaries) < 2000
    alerts = sink.of(EventType.LIFE_PLANNER_ERROR, stop_kind="planner_repeated_failure")
    assert len(alerts) == 1
    assert alerts[0]["operator_alert"] is True
    degraded = sink.of(EventType.LIFE_DAEMON_DEGRADED, reason="planner_repeated_failure")
    assert len(degraded) == 1
    assert degraded[0]["objective_dispatched"] is True
    assert any(
        event.get("model_call_skipped") is True
        for event in sink.of(EventType.LIFE_PLANNER_WAITING)
    )


def test_first_ten_minutes_reach_the_alert_without_a_burst(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)

    summaries = _drive(supervisor, clock, 600.0)

    # Each failing cycle is one call plus one repair. Only the cycles up to
    # the alert reach the model; every later pass in the window is held.
    assert planner.planner_calls <= 2 * backoff.PLANNER_FAILURE_ALERT_AFTER
    assert sink.of(EventType.LIFE_PLANNER_ERROR, stop_kind="planner_repeated_failure")
    assert all(float(summary["suggested_sleep"] or 0) > 0 for summary in summaries)


def test_empty_plan_asks_once_and_waits_for_the_operator(tmp_path, monkeypatch) -> None:
    from argus.manager.directive import set_active_manager_directive

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _NOT_DONE_NO_TASKS)
    set_active_manager_directive(
        supervisor.memory.root, "questions are allowed", operator_question_policy="allow",
    )

    _drive(supervisor, clock, DAY_SECONDS)

    questions = [
        item for item in supervisor.memory.backlog.active()
        if item.title == "Planner needs operator direction"
    ]
    assert len(questions) == 1
    assert len(sink.of(EventType.LIFE_OPERATOR_QUESTION_PENDING)) == 1
    probes = int(DAY_SECONDS // backoff.PLANNER_FAILURE_HOLD_MAX_SECONDS) + 1
    # One cycle (call + repair) before asking, then only the rare re-probe.
    assert planner.planner_calls <= 2 * (1 + probes)


def test_operator_guidance_reaches_the_held_planner_at_once(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive(supervisor, clock, 3 * 3600.0)
    assert sink.of(EventType.LIFE_PLANNER_ERROR, stop_kind="planner_repeated_failure")
    held_calls = planner.planner_calls
    supervisor.run()
    assert planner.planner_calls == held_calls

    planner.reply = _ONE_TASK
    supervisor.test_inbox.append("write greet.py now")
    supervisor.run()

    assert planner.planner_calls > held_calls
    assert supervisor._planner_failure_streak == 0


def test_a_productive_turn_clears_the_streak(tmp_path, monkeypatch) -> None:
    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive(supervisor, clock, 30.0)
    assert supervisor._planner_failure_streak >= 1

    planner.reply = _ONE_TASK
    clock.now += backoff.PLANNER_FAILURE_BACKOFF_CAP_SECONDS
    assert supervisor._plan_next_work() is True

    assert supervisor._planner_failure_streak == 0
    assert supervisor._planner_failure_alerted_at is None


@pytest.mark.parametrize("streak", [2, 5, 30])
def test_held_cycle_never_calls_the_model(tmp_path, monkeypatch, streak) -> None:
    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    supervisor._planner_failure_streak = streak
    supervisor._planner_failure_same = 1
    supervisor._planner_failure_not_before = clock.now + 60.0

    summary = supervisor.run()

    assert planner.planner_calls == 0
    assert summary["stopped_by"] == "awaiting_external"
    assert summary["suggested_sleep"] >= 60.0 - 1e-6
