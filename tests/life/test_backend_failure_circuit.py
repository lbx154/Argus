"""Stop-losses for repeating backend failures.

Two regressions from one 48-hour billing window:

* 353 error/denied outcomes cost $123 because every backend failure was
  treated as an independent accident: fail fast, hand the mission back, pay
  for a replan, redispatch into the same failure. A run of IDENTICAL failures
  is one continuing cause, so the round loop now holds the same round and
  backs off — exponentially up to an hour once the cause has repeated three
  times — with an operator-visible event on every held retry.

* 89 of those retries were one misconfigured model name ("gpt-5.6-sol"):
  the model-configuration pause auto-resumed forever because nothing
  distinguished a standing misconfiguration from a provider having a bad
  minute. The distinction is repetition: after three consecutive cooldown
  pauses with the same normalized error, a model-configuration failure is
  treated as permanent — the mission parks on the operator with a plain
  question instead of being resumed and re-billed.
"""

from __future__ import annotations

import time as real_time
from pathlib import Path
from types import SimpleNamespace

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer import round_execution as round_execution_module
from argus_skill.engineer.round_stop_signals import (
    BACKEND_FAILURE_BACKOFF_CAP_SECONDS,
    backend_failure_hold_backoff_seconds,
    backend_failure_signature,
)
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor._mission_execution_helpers import _MissionRunState
from argus_skill.life.supervisor._mission_execution_runtime import (
    MissionExecutionRuntimeMixin,
)
from argus_skill.reviewer import ReviewerConfig

# --------------------------------------------------------------------------- #
# Normalization + backoff units
# --------------------------------------------------------------------------- #


def test_signature_groups_failures_that_differ_only_in_numbers() -> None:
    first = backend_failure_signature("Too Many Requests 429 (retry after 7s)")
    second = backend_failure_signature("Too Many Requests 429 (retry after 12s)")
    other = backend_failure_signature("gateway timeout")

    assert first == second
    assert first != other
    assert backend_failure_signature(None, exit_code=2) == "exit=#"


def test_hold_backoff_grows_and_caps_at_the_hour() -> None:
    seconds = [
        backend_failure_hold_backoff_seconds(
            same_cause_streak=streak, base_backoff_seconds=15.0
        )
        for streak in range(3, 12)
    ]
    assert seconds[:4] == [60.0, 120.0, 240.0, 480.0]
    assert max(seconds) == BACKEND_FAILURE_BACKOFF_CAP_SECONDS
    assert seconds[-1] == BACKEND_FAILURE_BACKOFF_CAP_SECONDS


# --------------------------------------------------------------------------- #
# Round loop: three identical failures open the circuit; two do not
# --------------------------------------------------------------------------- #


class _ScriptedEngineer:
    def __init__(self, results: list[RunnerResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def run_exec(self, **_kwargs) -> RunnerResult:
        self.calls += 1
        return self.results[min(self.calls, len(self.results)) - 1]


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs) -> ReviewDecision:
        self.calls += 1
        return ReviewDecision(status="done", reason="ok", next_action="")


class _ScriptedReviewer:
    def __init__(self, decisions: list[ReviewDecision]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def evaluate(self, **_kwargs) -> ReviewDecision:
        self.calls += 1
        return self.decisions[min(self.calls, len(self.decisions)) - 1]


class _StoppableEngineer(_ScriptedEngineer):
    """A scripted backend that carries the daemon's stop/abort provider.

    The real ``AgentCliBackend`` holds the composed interrupt reason provider
    (daemon stop event + operator abort mailbox) on
    ``_default_interrupt_reason_provider``; the round loop consults it while
    holding between failed rounds. Each hold check consumes one entry from
    ``interrupt_reasons``, mirroring how the abort mailbox is consumed.
    """

    def __init__(
        self, results: list[RunnerResult], interrupt_reasons: list[str | None]
    ) -> None:
        super().__init__(results)
        self._interrupt_reasons = list(interrupt_reasons)

    def _default_interrupt_reason_provider(self) -> str | None:
        if not self._interrupt_reasons:
            return None
        return self._interrupt_reasons.pop(0)


def _rate_limited(seconds: int) -> RunnerResult:
    return RunnerResult(
        exit_code=1,
        agent_messages=[],
        fatal_error=f"Too Many Requests 429 (retry after {seconds}s)",
    )


def _run(
    tmp_path: Path,
    monkeypatch,
    *,
    engineer: _ScriptedEngineer,
    reviewer,
    max_rounds: int,
    backoff_seconds: float = 0.0,
) -> tuple[str, list[dict], list[float], _ScriptedEngineer]:
    sleeps: list[float] = []
    monkeypatch.setattr(
        round_execution_module,
        "time",
        SimpleNamespace(
            time=real_time.time,
            monotonic=real_time.monotonic,
            sleep=sleeps.append,
        ),
    )
    events: list[dict] = []
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    status, _rounds, _message, _reason, _tid = engine.run(
        objective="run the benchmark",
        engineer_prompt_builder=lambda _next, _static=True: "run it",
        supervised_config=SupervisedConfig(
            max_rounds=max_rounds,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=backoff_seconds,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )
    return status, events, sleeps, engineer


def _backoff_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "round.backend_failure.backoff"]


def test_third_identical_failure_opens_the_circuit_with_an_hour_scale_hold(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer(
        [_rate_limited(7), _rate_limited(12), _rate_limited(30), _rate_limited(45)]
    )
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=_DoneReviewer(),
        max_rounds=4,
    )

    # The third identical failure holds with exponential backoff and an
    # operator-visible event; the bounded round budget still ends the run.
    assert engineer.calls == 4
    assert status == "error"
    backoffs = _backoff_events(events)
    assert len(backoffs) == 1
    assert backoffs[0]["operator_alert"] is True
    assert backoffs[0]["same_cause_streak"] == 3
    assert backoffs[0]["seconds"] == 60.0
    assert "times in a row" in backoffs[0]["text"]
    # The hold sleeps in short slices so a stop or abort signal can wake it.
    assert sleeps == [10.0] * 6


def test_two_identical_failures_do_not_open_the_circuit_and_recovery_resets(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer([
        _rate_limited(7),
        _rate_limited(12),
        RunnerResult(exit_code=0, agent_messages=["ran the benchmark"]),
    ])
    reviewer = _DoneReviewer()
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=reviewer,
        max_rounds=5,
    )

    # Before the hold existed, the second failure already ended the mission
    # in "error" and pushed the cost into a replanning cycle. The identical
    # second failure now retries once more, the backend recovers, and the
    # mission completes — with no operator alarm along the way.
    assert status == "done"
    assert engineer.calls == 3
    assert reviewer.calls == 1
    assert all(not e.get("operator_alert") for e in _backoff_events(events))
    assert sleeps == []


def test_mixed_failure_signatures_keep_the_ordinary_fail_fast(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(exit_code=1, agent_messages=[], fatal_error="gateway timeout"),
        RunnerResult(
            exit_code=1, agent_messages=[], fatal_error="connection reset by peer"
        ),
    ])
    status, _events, _sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=_DoneReviewer(),
        max_rounds=5,
    )

    # Two DIFFERENT failures carry no evidence of one continuing cause; the
    # configured threshold ends the mission exactly as before.
    assert status == "error"
    assert engineer.calls == 2


def test_a_successful_round_restarts_the_same_cause_count(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer([
        _rate_limited(7),
        _rate_limited(12),
        RunnerResult(exit_code=0, agent_messages=["ran the first half"]),
        _rate_limited(30),
        _rate_limited(45),
        RunnerResult(exit_code=0, agent_messages=["ran the second half"]),
    ])
    reviewer = _ScriptedReviewer([
        ReviewDecision(
            status="continue",
            reason="The first half ran; the second half remains.",
            next_action="Run the second half of the benchmark.",
        ),
        ReviewDecision(status="done", reason="ok", next_action=""),
    ])
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=reviewer,
        max_rounds=8,
        backoff_seconds=0.5,
    )

    # A round that reached review was not a backend failure, so the run of
    # identical failures ended there: when the same rate-limit signature
    # returns later, it is a new count starting at one, not the third of a
    # "consecutive" run — and the circuit stays closed for these scattered,
    # isolated accidents.
    assert status == "done"
    assert engineer.calls == 6
    assert reviewer.calls == 2
    assert [e["same_cause_streak"] for e in _backoff_events(events)] == [1, 2, 1, 2]
    assert all(not e.get("operator_alert") for e in _backoff_events(events))
    assert sleeps == [0.5] * 4


def test_a_wait_round_restarts_the_same_cause_count(
    tmp_path: Path, monkeypatch,
) -> None:
    import json
    import time as _time

    from argus_skill.engineer import runner as runner_module

    registry = tmp_path / ".argus_external_work"
    registry.mkdir()
    (registry / "job-1.json").write_text(json.dumps({
        "version": 1,
        "work_id": "job-1",
        "state": "running_healthy",
        "heartbeat_at": _time.time(),
        "stale_after_seconds": 300,
        "poll_after_seconds": 30,
        "description": "benchmark",
    }), encoding="utf-8")
    # The wait ends because the external work finished, not because the
    # cadence ran out, so the round loop continues in the same mission.
    monkeypatch.setattr(
        runner_module,
        "_run_external_work_wait",
        lambda **_kwargs: ("succeeded", 5.0),
    )
    engineer = _ScriptedEngineer([
        _rate_limited(7),
        _rate_limited(12),
        RunnerResult(
            exit_code=0,
            agent_messages=[
                "benchmark launched; waiting on it\n"
                '{"wait_for": "external_work", "wait_id": "job-1"}'
            ],
        ),
        _rate_limited(30),
        _rate_limited(45),
        RunnerResult(exit_code=0, agent_messages=["collected the results"]),
    ])
    reviewer = _DoneReviewer()
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=reviewer,
        max_rounds=8,
        backoff_seconds=0.5,
    )

    # The round that asked to wait on job-1 completed its turn; it was not a
    # backend failure, so the run of identical rate-limit failures ended
    # there. When the same signature returns after the wait, it is a new
    # count starting at one — the circuit stays closed and no hour-scale
    # hold opens for these scattered accidents.
    assert status == "done"
    assert engineer.calls == 6
    assert reviewer.calls == 1
    assert [e["same_cause_streak"] for e in _backoff_events(events)] == [1, 2, 1, 2]
    assert all(not e.get("operator_alert") for e in _backoff_events(events))
    assert sleeps == [0.5] * 4


_TURN_CAP_RECEIPT = (
    "Provider turn cap reached: this engineer-r3 call used 40 provider turns "
    "(allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP). Each further turn would "
    "resend the whole grown transcript; the harness continues this work in a "
    "fresh session instead."
)


def test_a_turn_cap_restart_restarts_the_same_cause_count(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer([
        _rate_limited(7),
        _rate_limited(12),
        RunnerResult(
            exit_code=-15,
            agent_messages=["ran half the benchmark before the allowance ended"],
            thread_id=None,
            fatal_error=_TURN_CAP_RECEIPT,
            stop_kind="backend_unavailable",
        ),
        _rate_limited(30),
        _rate_limited(45),
        RunnerResult(exit_code=0, agent_messages=["ran the other half"]),
    ])
    reviewer = _DoneReviewer()
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=reviewer,
        max_rounds=8,
        backoff_seconds=0.5,
    )

    # A call that used its whole per-call provider-turn allowance is routine
    # housekeeping, not a backend failure: it ends the run of identical
    # failures the same way a reviewed round does. The rate limit that
    # returns after the restart is a new count starting at one, not the
    # third of a "consecutive" run.
    assert status == "done"
    assert engineer.calls == 6
    assert reviewer.calls == 1
    restarts = [
        e for e in events if e.get("type") == "round.provider_turn_cap.restart"
    ]
    assert [e["streak"] for e in restarts] == [1]
    assert [e["same_cause_streak"] for e in _backoff_events(events)] == [1, 2, 1, 2]
    assert all(not e.get("operator_alert") for e in _backoff_events(events))
    assert sleeps == [0.5] * 4


# --------------------------------------------------------------------------- #
# Round loop: a stop or abort signal wakes the hold instead of waiting it out
# --------------------------------------------------------------------------- #


def _hold_interrupt_events(events: list[dict]) -> list[dict]:
    return [
        e
        for e in events
        if e.get("type") == "round.backend_failure.hold_interrupted"
    ]


def test_daemon_stop_ends_the_hold_before_it_sleeps(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _StoppableEngineer(
        [_rate_limited(7), _rate_limited(12), _rate_limited(30)],
        interrupt_reasons=["daemon stop requested"],
    )
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=_DoneReviewer(),
        max_rounds=8,
    )

    # The third identical failure opens the hour-scale hold, but the daemon
    # was already asked to stop: the mission pauses for the shutdown at once
    # instead of sleeping out the whole backoff first.
    assert engineer.calls == 3
    assert status == "paused_daemon_shutdown"
    assert sleeps == []
    interrupted = _hold_interrupt_events(events)
    assert len(interrupted) == 1
    assert interrupted[0]["stop_kind"] == "daemon_shutdown"
    assert "daemon stop requested" in interrupted[0]["text"]


def test_operator_abort_wakes_the_hold_mid_sleep(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _StoppableEngineer(
        [_rate_limited(7), _rate_limited(12), _rate_limited(30)],
        interrupt_reasons=[
            None,
            None,
            "operator abort requested: stop spending on this",
        ],
    )
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=_DoneReviewer(),
        max_rounds=8,
    )

    # The abort arrives two slices into a 60-second hold: the hold wakes at
    # the next check instead of sleeping the remaining 40 seconds, and the
    # mission ends as aborted with the operator's reason.
    assert engineer.calls == 3
    assert status == "aborted"
    assert sleeps == [10.0, 10.0]
    interrupted = _hold_interrupt_events(events)
    assert len(interrupted) == 1
    assert interrupted[0]["stop_kind"] == "operator_abort"
    assert "stop spending on this" in interrupted[0]["text"]


# --------------------------------------------------------------------------- #
# Life level: a repeating model-configuration cooldown becomes permanent
# --------------------------------------------------------------------------- #

_MODEL_CONFIG_REASON = (
    "Configured model is unavailable; Engineer and Reviewer were not run. "
    'error=Error: Model "gpt-5.6-sol" from --model flag is not available.'
)


class _RuntimeHarness(MissionExecutionRuntimeMixin):
    def __init__(self, memory: LifeMemory) -> None:
        self.memory = memory
        self.events: list[dict] = []

    def _emit(self, event) -> None:
        self.events.append(dict(event))


def _cooldown_state(item: BacklogItem, stop_reason: str) -> _MissionRunState:
    state = _MissionRunState(item)
    state.stop_reason = stop_reason
    state.stop_kind = "provider_cooldown"
    state.outcome = SimpleNamespace(final_review_status="")
    state.usage_summary = SimpleNamespace(pricing_status="priced")
    return state


def _fresh_item(memory: LifeMemory, item_id: str) -> BacklogItem:
    return next(row for row in memory.backlog.active() if row.id == item_id)


def test_third_identical_model_configuration_cooldown_parks_on_the_operator(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(
        BacklogItem.new(title="prove the bound", objective="prove it")
    )
    harness = _RuntimeHarness(memory)

    first = harness._maybe_park_permanent_provider_failure(
        _cooldown_state(_fresh_item(memory, item.id), _MODEL_CONFIG_REASON)
    )
    second = harness._maybe_park_permanent_provider_failure(
        _cooldown_state(_fresh_item(memory, item.id), _MODEL_CONFIG_REASON)
    )
    assert first is None and second is None
    assert _fresh_item(memory, item.id).provider_cooldown_streak == 2

    third = harness._maybe_park_permanent_provider_failure(
        _cooldown_state(_fresh_item(memory, item.id), _MODEL_CONFIG_REASON)
    )

    assert third is not None
    assert third["status"] == "paused_operator"
    parked = _fresh_item(memory, item.id)
    assert parked.status == "paused_operator"
    assert parked.provider_cooldown_streak == 3
    assert "stopped retrying this configuration" in parked.pending_question
    disabled = [
        event
        for event in harness.events
        if event["type"] == "life.mission.provider_configuration_disabled"
    ]
    assert len(disabled) == 1
    assert disabled[0]["operator_alert"] is True
    assert disabled[0]["streak"] == 3

    # The whole point: the provider-cooldown auto-resume no longer touches it.
    resumed = memory.backlog.resume_paused_statuses({
        "paused_provider_cooldown",
        "paused_provider_fence",
        "paused_daemon_shutdown",
    })
    assert resumed == []
    assert _fresh_item(memory, item.id).status == "paused_operator"


def test_a_different_failure_signature_restarts_the_count(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(
        BacklogItem.new(title="prove the bound", objective="prove it")
    )
    harness = _RuntimeHarness(memory)

    for _ in range(2):
        assert harness._maybe_park_permanent_provider_failure(
            _cooldown_state(_fresh_item(memory, item.id), _MODEL_CONFIG_REASON)
        ) is None

    # The provider comes back with a different complaint (letters differ, so
    # the normalized signature differs): the streak restarts instead of
    # parking a configuration that just lived through two separate outages.
    other_reason = _MODEL_CONFIG_REASON.replace("gpt-5.6-sol", "gpt-nova")
    assert harness._maybe_park_permanent_provider_failure(
        _cooldown_state(_fresh_item(memory, item.id), other_reason)
    ) is None

    refreshed = _fresh_item(memory, item.id)
    assert refreshed.provider_cooldown_streak == 1
    assert refreshed.status != "paused_operator"
    assert not [
        event
        for event in harness.events
        if event["type"] == "life.mission.provider_configuration_disabled"
    ]


def test_repeating_non_model_cooldowns_keep_the_ordinary_auto_resume(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(
        BacklogItem.new(title="prove the bound", objective="prove it")
    )
    harness = _RuntimeHarness(memory)
    outage = "provider capacity exhausted; cooling down before the next attempt"

    for expected_streak in (1, 2, 3, 4):
        assert harness._maybe_park_permanent_provider_failure(
            _cooldown_state(_fresh_item(memory, item.id), outage)
        ) is None
        assert (
            _fresh_item(memory, item.id).provider_cooldown_streak
            == expected_streak
        )
    assert _fresh_item(memory, item.id).status != "paused_operator"
