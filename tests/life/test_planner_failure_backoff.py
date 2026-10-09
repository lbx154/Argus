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
    """Answers every Planner call with ``reply`` until told otherwise.

    ``fatal`` (a string or a callable returning one) makes the call fail at
    the backend instead, the way a 5xx, a timeout or a watchdog kill does.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.planner_calls = 0
        self.fatal: Any = None

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label.startswith("planner.cycle"), run_label
        self.planner_calls += 1
        fatal = self.fatal() if callable(self.fatal) else self.fatal
        if fatal:
            return RunnerResult(
                exit_code=1,
                agent_messages=[],
                stdout_lines=[],
                stderr_lines=[],
                thread_id="planner-thread",
                fatal_error=fatal,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
            )
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
        self.now = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
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


def _supervisor(tmp_path: Path, monkeypatch, reply: str, *, clock: _Clock | None = None):
    """A continuous supervisor; called again on the same path it is a restart."""
    project = tmp_path / "project"
    if not project.exists():
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
    clock = clock or _Clock()
    monkeypatch.setattr(backoff, "time", clock)
    return supervisor, planner, sink, clock


def _pass(supervisor: LifeSupervisor) -> dict[str, Any]:
    summary = supervisor.run()
    supervisor._missions_started = 0
    supervisor._planning_cycles = 0
    return summary


def _drive(supervisor: LifeSupervisor, clock: _Clock, seconds: float) -> list[dict[str, Any]]:
    """Run daemon passes back to back, sleeping what each pass asks for."""
    deadline = clock.now + seconds
    summaries = []
    while clock.now < deadline:
        summary = _pass(supervisor)
        summaries.append(summary)
        clock.now += max(POLL_SECONDS, float(summary.get("suggested_sleep") or 0.0))
    return summaries


def _seconds_until_next_call(supervisor, planner, clock, limit: float) -> float | None:
    calls, start = planner.planner_calls, clock.now
    while clock.now - start < limit:
        summary = _pass(supervisor)
        if planner.planner_calls > calls:
            return clock.now - start
        clock.now += max(POLL_SECONDS, float(summary.get("suggested_sleep") or 0.0))
    return None


def _drive_until_alert(supervisor, sink, clock, limit: float = 3600.0) -> None:
    """Stop right after the alert, while the hold is fresh."""
    deadline = clock.now + limit
    while not _alerts(sink):
        assert clock.now < deadline, "no alert"
        summary = _pass(supervisor)
        if _alerts(sink):
            return
        clock.now += max(POLL_SECONDS, float(summary.get("suggested_sleep") or 0.0))


def _assert_held(supervisor, planner, clock) -> None:
    calls = planner.planner_calls
    clock.now += POLL_SECONDS
    _pass(supervisor)
    assert planner.planner_calls == calls, "an unchanged project must stay held"


def _alerts(sink: _Sink) -> list[dict[str, Any]]:
    return sink.of(EventType.LIFE_PLANNER_ERROR, stop_kind="planner_repeated_failure")


def _streak(supervisor: LifeSupervisor) -> int:
    return int(supervisor._load_planner_failure_state().get("streak", 0) or 0)


CAP = backoff.PLANNER_FAILURE_BACKOFF_CAP_SECONDS


def test_backoff_starts_after_the_free_attempts_and_doubles_to_a_cap() -> None:
    low = [backoff.planner_failure_backoff_seconds(n, rand=lambda: 0.0) for n in range(1, 14)]
    high = [backoff.planner_failure_backoff_seconds(n, rand=lambda: 1.0) for n in range(1, 14)]

    free = backoff.PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS
    assert low[:free] == high[:free] == [0.0] * free
    base = backoff.PLANNER_FAILURE_BACKOFF_BASE_SECONDS
    assert high[free] == base and low[free] == base / 2
    assert high[free + 1] == 2 * base
    assert all(later >= earlier for earlier, later in zip(high, high[1:]))
    assert max(high) == CAP
    assert all(CAP / 2 <= value <= CAP for value in high[-3:])
    # Jitter spreads the wait but never below half the nominal delay.
    assert all(lo >= hi / 2 for lo, hi in zip(low, high))


def test_failure_key_ignores_ids_counters_and_quotes_but_not_the_failure() -> None:
    class Verdict:
        def __init__(self, error: str, reason: str = "") -> None:
            self.error = error
            self.reason = reason

    key = lambda text: backoff.planner_failure_key("planner_error", Verdict(text))  # noqa: E731
    assert key("repair exhausted after 1 attempt") == key("repair exhausted after 2 attempt")
    assert key("bad node 'x1' in plan 3f2a9c1d7e") == key("bad node 'other' in plan 9d8e7f6a5b")
    assert key(
        "missing marker request_id=req_AbCdEfGhIjKl trace 123e4567-e89b-12d3-a456-426614174000"
    ) == key("missing marker request_id=req_ZyXwVuTsRqPo trace 00000000-0000-0000-0000-000000000000")
    assert key("repair exhausted") != key("backend refused the call")
    # Every backend failure is one class; its wording is provider noise.
    backend = lambda text: backoff.planner_failure_key(  # noqa: E731
        "planner_error", Verdict(text, "planner backend failed before producing output"),
    )
    assert backend("upstream 500 req_abc") == backend("stream error: HTTP 503")


def test_unreadable_planner_replies_are_bounded_over_a_day(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)

    summaries = _drive(supervisor, clock, DAY_SECONDS)

    # A tight loop asked thousands of times a day. The paced loop makes a few
    # attempts, reports the repeat once, then re-asks at most once per hold.
    cycles = backoff.PLANNER_FAILURE_ALERT_AFTER + int(DAY_SECONDS // CAP) + 1
    assert planner.planner_calls <= 2 * cycles
    assert len(summaries) < 3000
    alerts = _alerts(sink)
    assert len(alerts) == 1
    assert alerts[0]["operator_alert"] is True
    assert alerts[0]["failure_kind"] == backoff.FAILURE_KIND_DECISION
    degraded = sink.of(EventType.LIFE_DAEMON_DEGRADED, reason="planner_repeated_failure")
    assert len(degraded) == 1
    assert degraded[0]["objective_dispatched"] is True


def test_first_ten_minutes_reach_the_alert_without_a_burst(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)

    summaries = _drive(supervisor, clock, 600.0)

    assert planner.planner_calls <= 2 * backoff.PLANNER_FAILURE_ALERT_AFTER
    assert _alerts(sink)
    assert all(float(summary["suggested_sleep"] or 0) > 0 for summary in summaries)


def test_varying_unreadable_replies_still_alert(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    replies = iter(f"garbage {n} {'x' * (n % 7)} id={n:08x}" for n in range(10_000))
    original = planner.run_exec

    def varied(**kwargs):
        planner.reply = next(replies)
        return original(**kwargs)

    monkeypatch.setattr(planner, "run_exec", varied)
    _drive(supervisor, clock, DAY_SECONDS)

    assert len(_alerts(sink)) == 1
    assert planner.planner_calls <= 2 * (backoff.PLANNER_FAILURE_ALERT_AFTER + int(DAY_SECONDS // CAP) + 1)


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
    assert planner.planner_calls <= 2 * (1 + int(DAY_SECONDS // CAP) + 1)


def test_operator_guidance_reaches_the_held_planner_at_once(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive_until_alert(supervisor, sink, clock)
    _assert_held(supervisor, planner, clock)
    held_calls = planner.planner_calls

    planner.reply = _ONE_TASK
    supervisor.test_inbox.append("write greet.py now")
    _pass(supervisor)

    assert planner.planner_calls > held_calls
    assert _streak(supervisor) == 0


def test_a_manager_directive_wakes_the_held_planner_at_the_next_cycle(tmp_path, monkeypatch) -> None:
    from argus.core.operator_context import operator_context_state_root
    from argus.manager.directive import set_active_manager_directive

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive_until_alert(supervisor, sink, clock)
    _assert_held(supervisor, planner, clock)
    planner.reply = _ONE_TASK
    set_active_manager_directive(
        operator_context_state_root(supervisor.memory),
        "Use format X; stop writing prose.",
        source="manager.supervision:abc123",
        scope_objective=supervisor.config.continuous_objective,
    )

    assert _seconds_until_next_call(supervisor, planner, clock, CAP) == 0.0


def test_new_project_evidence_wakes_the_held_planner_at_the_next_cycle(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive_until_alert(supervisor, sink, clock)
    _assert_held(supervisor, planner, clock)
    planner.reply = _ONE_TASK
    results = tmp_path / "project" / "results"
    results.mkdir()
    (results / "metrics.json").write_text('{"acc": 0.93}', encoding="utf-8")

    assert _seconds_until_next_call(supervisor, planner, clock, CAP) == 0.0


def test_a_planner_model_change_wakes_the_held_planner(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive_until_alert(supervisor, sink, clock)
    _assert_held(supervisor, planner, clock)
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_MODEL", "another-model")

    assert _seconds_until_next_call(supervisor, planner, clock, CAP) == 0.0


def test_unchanged_inputs_hold_no_longer_than_the_cap(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive_until_alert(supervisor, sink, clock)
    _assert_held(supervisor, planner, clock)

    waited = _seconds_until_next_call(supervisor, planner, clock, 8 * 3600.0)

    assert waited is not None and waited <= CAP


def test_backend_outage_backs_off_but_recovers_within_the_cap(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = "stream error: HTTP 503 Service Unavailable"
    _drive(supervisor, clock, 300.0)
    # A five-minute outage is not worth an alarm, and nothing is held for it.
    assert not _alerts(sink)
    planner.fatal = None

    waited = _seconds_until_next_call(supervisor, planner, clock, 8 * 3600.0)

    assert waited is not None and waited <= CAP
    assert _streak(supervisor) == 0


def test_sustained_backend_failure_alerts_once_and_keeps_probing(tmp_path, monkeypatch) -> None:
    import itertools

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    ids = itertools.count()
    planner.fatal = lambda: f"upstream 500 request_id=req_{next(ids):012x}"

    _drive(supervisor, clock, DAY_SECONDS)

    alerts = _alerts(sink)
    assert len(alerts) == 1
    assert alerts[0]["failure_kind"] == backoff.FAILURE_KIND_BACKEND
    # It never stops probing: about one call per cap, never a storm.
    assert DAY_SECONDS // CAP <= planner.planner_calls <= DAY_SECONDS // (CAP / 2) + 10


def test_a_superseded_turn_is_not_a_failure(tmp_path, monkeypatch) -> None:
    from argus.planner.planner import PLANNER_SUPERSEDED_ERROR

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = PLANNER_SUPERSEDED_ERROR
    for _ in range(4):
        _pass(supervisor)
        clock.now += 120.0

    assert _streak(supervisor) == 0
    assert not _alerts(sink)
    assert not sink.of(EventType.LIFE_DAEMON_DEGRADED)


def test_an_empty_plan_the_manager_reconciled_is_not_a_failure(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _NOT_DONE_NO_TASKS)
    monkeypatch.setattr(supervisor, "_reconcile_open_ended_terminal_stage_action", lambda _v: "advance")
    for _ in range(4):
        _pass(supervisor)
        clock.now += 120.0

    assert _streak(supervisor) == 0
    assert not _alerts(sink)


def test_a_restart_keeps_the_streak_and_does_not_alert_again(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive(supervisor, clock, 600.0)
    assert len(_alerts(sink)) == 1
    calls = planner.planner_calls

    for _ in range(3):
        restarted, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE, clock=clock)
        _drive(restarted, clock, 600.0)
        assert not _alerts(sink)
        calls += planner.planner_calls

    # Four ten-minute lives cost what one does plus at most a re-probe each.
    assert calls <= 2 * (backoff.PLANNER_FAILURE_ALERT_AFTER + 3)


def test_parallel_supervisors_share_one_streak_and_one_alert(tmp_path, monkeypatch) -> None:
    primary, planner_a, sink_a, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    helper, planner_b, sink_b, _ = _supervisor(tmp_path, monkeypatch, _UNREADABLE, clock=clock)
    deadline = clock.now + 3600.0
    while clock.now < deadline:
        sleeps = [_pass(primary), _pass(helper)]
        clock.now += max(POLL_SECONDS, min(float(s.get("suggested_sleep") or 0) or POLL_SECONDS for s in sleeps))

    assert len(_alerts(sink_a)) + len(_alerts(sink_b)) == 1
    assert planner_a.planner_calls + planner_b.planner_calls <= 2 * (
        backoff.PLANNER_FAILURE_ALERT_AFTER + 3
    )


def test_health_reads_degraded_while_held_after_the_alert(tmp_path, monkeypatch) -> None:
    from argus.daemon.health import DaemonHealthTracker, read_daemon_health

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive(supervisor, clock, 1500.0)
    assert _alerts(sink)
    tracker = DaemonHealthTracker(tmp_path / "health")
    for event in sink.events:
        tracker.observe(event)

    assert read_daemon_health(tmp_path / "health", pid=None, alive=True)["state"] == "degraded"


def test_a_mission_finishing_wakes_the_backoff_before_the_alert(tmp_path, monkeypatch) -> None:
    from argus.life.memory import BacklogItem

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    while _streak(supervisor) < backoff.PLANNER_FAILURE_ALERT_AFTER - 1:
        _pass(supervisor)
        clock.now += POLL_SECONDS
    assert not _alerts(sink)
    item = supervisor.memory.backlog.add(BacklogItem.new(title="side job", objective="x"))
    supervisor.memory.backlog.update(item.id, status="done")
    planner.reply = _ONE_TASK

    assert _seconds_until_next_call(supervisor, planner, clock, CAP) == 0.0


def test_a_productive_turn_clears_the_streak(tmp_path, monkeypatch) -> None:
    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    _drive(supervisor, clock, 30.0)
    assert _streak(supervisor) >= 1

    planner.reply = _ONE_TASK
    clock.now += CAP
    assert supervisor._plan_next_work() is True

    assert _streak(supervisor) == 0
    assert not supervisor._planner_failure_state_path().exists()


@pytest.mark.parametrize("streak", [3, 5, 30])
def test_held_cycle_never_calls_the_model(tmp_path, monkeypatch, streak) -> None:
    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _UNREADABLE)
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    state = _PlanCycleState(None)
    supervisor._save_planner_failure_state({
        "streak": streak,
        "same": 1,
        "key": "k",
        "kind": backoff.FAILURE_KIND_DECISION,
        "first_failed_at": clock.now,
        "last_failed_at": clock.now,
        "not_before": clock.now + 60.0,
        "alerted_at": None,
        "signature": supervisor._planner_failure_input_signature(state),
        "config_signature": supervisor._planner_failure_config_signature(),
    })

    summary = _pass(supervisor)

    assert planner.planner_calls == 0
    assert summary["stopped_by"] == "awaiting_external"
    assert summary["suggested_sleep"] >= 60.0 - 1e-6
