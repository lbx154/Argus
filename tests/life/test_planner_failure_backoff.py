"""Repeated failed Planner calls are paced, surfaced once, and never spin.

Each daemon pass is simulated with a fake clock that advances by the sleep the
supervisor asks for, so these tests measure model calls over wall-clock time
without sleeping.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.life.memory import LifeMemory
from argus.life.supervisor import _planner_failure_backoff as backoff
from argus.life.supervisor._config import LifeSupervisorConfig
from argus.life.supervisor._core import LifeSupervisor
from argus.manager import Manager
from tests.hand_off import plan, submit

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
        if self.reply:
            submit(options, self.reply)
        return RunnerResult(
            exit_code=0,
            stdout_lines=[],
            stderr_lines=[],
            thread_id="planner-thread",
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


# A turn that ends without handing off.
_UNREADABLE = None
_OUTAGE = "stream error: HTTP 503 Service Unavailable"
_NOT_DONE_NO_TASKS = plan(to='manager', summary='still thinking about the next step')
_ONE_TASK = plan(summary="one concrete step remains", advance_to_stage="delivery", tasks=[{
    "key": "write-script", "title": "Write the greeting script",
    "objective": "Write greet.py that prints hello and run it.",
    "scope": "bounded", "require_independent_review": False,
}])


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
    monkeypatch.setattr(supervisor, "_effective_final_certification_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(supervisor, "_planner_runtime_notes", lambda: "")
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


def _drive_until_alert(supervisor, sink, clock, limit: float = 2 * 3600.0) -> None:
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
WINDOW = backoff.AUTOMATIC_RETRY_WINDOW_SECONDS


def _stop_notices(supervisor: LifeSupervisor) -> list[dict[str, Any]]:
    from argus.core.transcript import read_turns

    return [
        turn for turn in read_turns(supervisor._manager_project_report_root())
        if str(turn.get("message_id") or "").startswith("argus-notice-planner-retries-stopped")
    ]


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


def test_calls_the_budget_refuses_are_not_failures(tmp_path, monkeypatch) -> None:
    """An idle Planner is asked until the daemon's background budget runs out,
    then waits at the idle backoff without a failure, an alert or a cost; the
    first progress lets it be asked again. Refused calls once counted as a
    backend outage: a false alert after 54 minutes, then 3 hours on hold."""
    from argus.core import call_guard

    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _NOT_DONE_NO_TASKS)
    answer = planner.run_exec

    def admitted(**kwargs: Any) -> RunnerResult:
        # The backend's admission (_exec_admission step 3).
        paused = call_guard.refusal(kwargs["run_label"])
        if paused:
            return RunnerResult(exit_code=-1, fatal_error=paused, stop_kind="operator_pause")
        return answer(**kwargs)

    monkeypatch.setattr(planner, "run_exec", admitted)
    trips: list[list[str]] = []
    call_guard.enable(on_trip=trips.append)
    try:
        sleeps = [float(s.get("suggested_sleep") or 0) for s in _drive(supervisor, clock, WINDOW + CAP)]
        assert (planner.planner_calls, len(trips)) == (call_guard.BACKGROUND_CALL_BUDGET, 1)
        assert min(sleeps) >= 15.0 and max(sleeps) <= CAP
        assert _streak(supervisor) == 0
        assert not sink.of(EventType.LIFE_PLANNER_ERROR)
        assert not sink.of(EventType.LIFE_DAEMON_DEGRADED)
        assert not _stop_notices(supervisor)

        call_guard.observe({"type": EventType.LIFE_INBOX_DRAINED})
        assert _seconds_until_next_call(supervisor, planner, clock, CAP + POLL_SECONDS) is not None
    finally:
        call_guard.disable()


def test_parallel_supervisors_share_one_streak_and_one_alert(tmp_path, monkeypatch) -> None:
    primary, planner_a, sink_a, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    helper, planner_b, sink_b, _ = _supervisor(tmp_path, monkeypatch, _ONE_TASK, clock=clock)
    planner_a.fatal = planner_b.fatal = _OUTAGE
    deadline = clock.now + 3600.0
    while clock.now < deadline:
        sleeps = [_pass(primary), _pass(helper)]
        clock.now += max(POLL_SECONDS, min(float(s.get("suggested_sleep") or 0) or POLL_SECONDS for s in sleeps))

    assert len(_alerts(sink_a)) + len(_alerts(sink_b)) == 1
    # One shared streak: together they pace like one supervisor, not two.
    assert _streak(primary) == planner_a.planner_calls + planner_b.planner_calls


def _write_state(supervisor, **fields) -> None:
    path = supervisor._planner_failure_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "objective": supervisor._planner_failure_objective(), **fields}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"streak": 3, "not_before": "soon", "first_failed_at": 0, "last_failed_at": 0,
         "kind": "backend"},
        {"streak": 3, "not_before": float("nan"), "first_failed_at": 0, "last_failed_at": 0,
         "kind": "backend"},
        {"streak": 3, "not_before": 1e300, "first_failed_at": 0, "last_failed_at": 0,
         "kind": "no-such-kind"},
        {"streak": 3, "first_failed_at": 0, "kind": "backend"},
        # A streak of unusable answers an older version paced.
        {"streak": 3, "not_before": 1e300, "first_failed_at": 0, "last_failed_at": 0,
         "kind": "decision"},
    ],
)
def test_wrongly_typed_fields_count_as_an_empty_streak(tmp_path, monkeypatch, fields) -> None:
    supervisor, planner, _sink, _clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    _write_state(supervisor, config_signature=supervisor._planner_failure_config_signature(), **fields)

    _pass(supervisor)

    assert planner.planner_calls == 1


@pytest.mark.parametrize("kind", [backoff.FAILURE_KIND_BACKEND, backoff.FAILURE_KIND_INTERNAL])
def test_a_clock_stepped_back_cannot_stretch_the_hold(tmp_path, monkeypatch, kind) -> None:
    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    future = clock.now + 3 * DAY_SECONDS
    _write_state(
        supervisor,
        streak=5,
        kind=kind,
        first_failed_at=future,
        last_failed_at=future,
        not_before=future,
        alerted_at=future,
        config_signature=supervisor._planner_failure_config_signature(),
    )

    waited = _seconds_until_next_call(supervisor, planner, clock, 5 * DAY_SECONDS)

    assert waited is not None and waited <= CAP


def test_an_alert_already_in_the_event_log_is_not_sent_again(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = _OUTAGE
    _drive_until_alert(supervisor, sink, clock)
    record = supervisor._load_planner_failure_state()
    alert_id = record["alert"]["id"]
    # The alert reached the log, then the process died before clearing the flag.
    record["alert_pending"] = True
    supervisor._save_planner_failure_state(record)
    events = supervisor._planner_failure_state_path().parent / "events.jsonl"
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "life.planner.error", "alert_id": alert_id}) + "\n")

    restarted, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK, clock=clock)
    planner.fatal = _OUTAGE
    _drive(restarted, clock, 3600.0)

    assert not _alerts(sink)
    assert not restarted._load_planner_failure_state().get("alert_pending")


def test_a_stuck_lock_holder_does_not_stall_planning(tmp_path, monkeypatch) -> None:
    import portalocker

    supervisor, planner, _sink, _clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = _OUTAGE
    monkeypatch.setattr(backoff, "_LOCK_WAIT_SECONDS", 0.2)
    lock_path = supervisor._planner_failure_state_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = __import__("time").monotonic()
    with lock_path.open("a+b") as holder:
        portalocker.lock(holder, portalocker.LOCK_EX)
        _pass(supervisor)
        portalocker.unlock(holder)

    assert planner.planner_calls >= 1
    assert __import__("time").monotonic() - started < 30.0


def test_an_argus_bug_is_reported_as_an_argus_fault(tmp_path, monkeypatch) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)

    def broken(**_kwargs):
        planner.planner_calls += 1
        raise KeyError("missing 'stage'")

    monkeypatch.setattr(planner, "run_exec", broken)
    _drive(supervisor, clock, DAY_SECONDS)

    alerts = _alerts(sink)
    assert len(alerts) == 1
    assert alerts[0]["failure_kind"] == backoff.FAILURE_KIND_INTERNAL
    text = sink.of(EventType.LIFE_DAEMON_DEGRADED)[0]["text"]
    assert "internal error in Argus" in text and "model service" not in text
    # It probes at the cap for the retry window, then stops and says so.
    assert WINDOW // CAP <= planner.planner_calls <= WINDOW // (CAP / 2) + 10
    assert len(_stop_notices(supervisor)) == 1
    # A planning error raised inside Argus itself is the same class.
    assert backoff.planner_failure_kind(None) == backoff.FAILURE_KIND_INTERNAL


def test_unusable_answers_are_the_planners_business(tmp_path, monkeypatch) -> None:
    """An answered call is never counted, held or alerted on, and it ends a
    backend streak: the backend is evidently answering again."""
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = _OUTAGE
    _drive(supervisor, clock, 300.0)
    assert _streak(supervisor) >= 1

    planner.fatal = None
    planner.reply = _UNREADABLE
    _drive(supervisor, clock, WINDOW + CAP)

    assert _streak(supervisor) == 0
    assert not _alerts(sink)
    assert not _stop_notices(supervisor)


@pytest.mark.parametrize("wake", ["operator", "model"])
def test_a_stopped_backend_retry_comes_back_for_the_operator_or_a_new_model(
    tmp_path, monkeypatch, wake,
) -> None:
    supervisor, planner, sink, clock = _supervisor(tmp_path, monkeypatch, _ONE_TASK)
    planner.fatal = "stream error: HTTP 503 Service Unavailable"
    _drive(supervisor, clock, WINDOW + CAP)
    _assert_held(supervisor, planner, clock)
    stopped_calls = planner.planner_calls
    planner.fatal = None

    if wake == "operator":
        supervisor.test_inbox.append("the backend is back, carry on")
    else:
        monkeypatch.setenv("ARGUS_SKILL_PLANNER_MODEL", "another-model")
    _pass(supervisor)

    assert planner.planner_calls == stopped_calls + 1
    assert _streak(supervisor) == 0


def test_a_provider_outage_holds_the_planner_until_a_probe_is_answered(tmp_path, monkeypatch) -> None:
    """Every task is fenced: the circuit the fence opened keeps the Planner
    uncalled, each half-open window lets one Planner probe through, and the
    first probe the model service answers closes the circuit and puts the
    fenced task back in the queue."""
    import time

    from argus.core.autonomy import RETRY_WINDOW_SPENT_REASON
    from argus.life.memory import BacklogItem
    from argus.life.runtime_failure_circuit import (
        CIRCUIT_FILENAME,
        PROVIDER_OUTAGE,
        active_runtime_failure_circuit,
    )

    supervisor, planner, _sink, clock = _supervisor(tmp_path, monkeypatch, _NOT_DONE_NO_TASKS)
    planner.fatal = _OUTAGE
    life = tmp_path / "life"
    missions: list[int] = []

    def execute(**_kwargs: Any) -> Any:
        missions.append(1)
        if len(missions) == 1:
            return SimpleNamespace(
                success=False, status="parked", park_reason="provider_fence",
                stop_kind="provider_fence", rounds=1,
                stop_reason=f"{RETRY_WINDOW_SPENT_REASON} (HTTP 503).",
            )
        return SimpleNamespace(success=True, status="done", stop_reason="", rounds=1)

    def open_window() -> None:
        # What a changed backend, model or operator input, or the timed
        # fallback does to the circuit.
        state = json.loads((life / CIRCUIT_FILENAME).read_text())
        state["canary_due_at"] = time.time() - 1
        (life / CIRCUIT_FILENAME).write_text(json.dumps(state))

    supervisor.runner.execute = execute
    task = supervisor.memory.backlog.add(BacklogItem.new(
        title="greet", objective="write greet.py",
        manager_decision={"routed": True, "vertical": "software"},
    ))

    _drive(supervisor, clock, 3600.0)
    assert active_runtime_failure_circuit(life)["fingerprint"] == PROVIDER_OUTAGE
    assert (len(missions), planner.planner_calls) == (1, 0)

    open_window()
    _drive(supervisor, clock, 3600.0)
    # The probe met the outage: one call, and the circuit stays open.
    assert planner.planner_calls == 1
    assert active_runtime_failure_circuit(life) is not None

    planner.fatal = None
    open_window()
    _pass(supervisor)
    assert planner.planner_calls > 1
    assert active_runtime_failure_circuit(life) is None
    row = next(row for row in supervisor.memory.backlog.all() if row.id == task.id)
    assert (row.status, row.attempt) == ("pending", 2)

    _pass(supervisor)
    assert len(missions) == 2
    assert next(row for row in supervisor.memory.backlog.all() if row.id == task.id).status == "done"
