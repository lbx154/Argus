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

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer import round_execution as round_execution_module
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor._mission_execution_helpers import _MissionRunState
from argus.life.supervisor._mission_execution_runtime import (
    MissionExecutionRuntimeMixin,
)
from argus.reviewer import ReviewerConfig

# --------------------------------------------------------------------------- #
# Normalization + backoff units
# --------------------------------------------------------------------------- #


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
    """A session the provider ended the same way each time.

    The record is a session death — the CLI exited without naming a cause —
    which is what the failure streak and its hold are for. A record that
    names an unreachable service (see ``_unreachable``) never reaches the
    streak: it pauses for a provider cooldown instead.
    """
    return RunnerResult(
        exit_code=2,
        agent_messages=[],
        fatal_error=(
            "Process exited with code 2 before turn completion.\n"
            f"turn ended after {seconds}s without a result"
        ),
    )


def _unreachable(port: int) -> RunnerResult:
    return RunnerResult(
        exit_code=1,
        agent_messages=[],
        fatal_error=(
            "Copilot CLI exited with code 1.\n"
            f"Error: connect ECONNREFUSED 127.0.0.1:{port}\n"
            "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)"
        ),
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
        ),
        workdir=tmp_path,
        on_event=events.append,
    )
    return status, events, sleeps, engineer


def _backoff_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "round.backend_failure.backoff"]


# --------------------------------------------------------------------------- #
# Round loop: an unreachable service pauses for a cooldown, not the streak
# --------------------------------------------------------------------------- #


def test_an_unreachable_model_service_pauses_instead_of_burning_the_streak(
    tmp_path: Path, monkeypatch,
) -> None:
    engineer = _ScriptedEngineer([_unreachable(18765), _unreachable(18765)])
    status, events, sleeps, engineer = _run(
        tmp_path,
        monkeypatch,
        engineer=engineer,
        reviewer=_DoneReviewer(),
        max_rounds=4,
        backoff_seconds=0.5,
    )

    # One call, then a provider-cooldown pause: no fresh-session retry, no
    # backoff, no streak. A relay that had died inside a container once cost
    # hours of retries this way with the operator seeing only "exit code 1".
    assert status == "parked"
    assert engineer.calls == 1
    assert sleeps == []
    assert _backoff_events(events) == []
    review = next(e for e in events if e.get("type") == "round.review.completed")
    assert review["review_skipped"] is True
    assert review["stop_kind"] == "provider_cooldown"
    assert review["failure_kind"] == "service_unreachable"
    assert review["failure_cause"] == "connect ECONNREFUSED 127.0.0.1:18765"
    assert review["reason"].startswith(
        "The model service could not be reached: connect ECONNREFUSED 127.0.0.1:18765. "
    )


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
    assert status == "parked"
    assert sleeps == []
    interrupted = _hold_interrupt_events(events)
    assert len(interrupted) == 1
    assert interrupted[0]["stop_kind"] == "daemon_shutdown"
    assert "daemon stop requested" in interrupted[0]["text"]


# --------------------------------------------------------------------------- #
# Life level: a repeating model-configuration cooldown becomes permanent
# --------------------------------------------------------------------------- #


class _RuntimeHarness(MissionExecutionRuntimeMixin):
    def __init__(self, memory: LifeMemory) -> None:
        self.memory = memory
        self.events: list[dict] = []

    def _emit(self, event) -> None:
        self.events.append(dict(event))

    def _operator_chinese(self) -> bool:
        return False

    def _project_state_root(self) -> Path:
        return Path(self.memory.root)


def _cooldown_state(item: BacklogItem, stop_reason: str) -> _MissionRunState:
    state = _MissionRunState(item)
    state.stop_reason = stop_reason
    state.stop_kind = "provider_cooldown"
    state.outcome = SimpleNamespace(final_review_status="")
    state.usage_summary = SimpleNamespace(pricing_status="priced")
    return state


def _fresh_item(memory: LifeMemory, item_id: str) -> BacklogItem:
    return next(row for row in memory.backlog.active() if row.id == item_id)


# --------------------------------------------------------------------------- #
# Automatic retries stop after the retry window
# --------------------------------------------------------------------------- #


class _BoundedEngineer(_ScriptedEngineer):
    """Fails the same way every time; a loop that never stops fails the test."""

    def run_exec(self, **kwargs) -> RunnerResult:
        assert self.calls < 100, "the hold never stopped retrying"
        return super().run_exec(**kwargs)


def test_a_same_cause_hold_retries_for_three_hours_then_fences_the_task(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core.autonomy import AUTOMATIC_RETRY_WINDOW_SECONDS, RETRY_WINDOW_SPENT_REASON

    clock = [1_000.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(
        round_execution_module,
        "time",
        SimpleNamespace(time=lambda: clock[0], monotonic=real_time.monotonic, sleep=sleep),
    )
    engineer = _BoundedEngineer([_rate_limited(7)])
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=_DoneReviewer(),
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    status, rounds, _message, reason, _tid = engine.run(
        objective="run the benchmark",
        engineer_prompt_builder=lambda _next, _static=True: "run it",
        supervised_config=SupervisedConfig(
            max_rounds=0,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=15.0,
        ),
        workdir=tmp_path,
        on_event=lambda _event: None,
    )

    assert status == "parked"
    assert rounds[-1].stop_kind == "provider_fence"
    assert reason.startswith(RETRY_WINDOW_SPENT_REASON)
    assert clock[0] - 1_000.0 >= AUTOMATIC_RETRY_WINDOW_SECONDS
    # Backing off to the hour scale, three hours hold about ten attempts.
    assert engineer.calls == 11


def _settle_cooldown(memory: LifeMemory, item_id: str, state: _MissionRunState) -> None:
    """What the pause settlement writes after the parking check."""
    from argus.life.mission_outcome import mission_outcome_dimensions

    status = "paused_provider_fence" if state.stop_kind == "provider_fence" else (
        "paused_provider_cooldown"
    )
    memory.backlog.update(
        item_id,
        status=status,
        last_error=state.stop_reason,
        outcome=mission_outcome_dimensions(
            status=status, success=False, stop_kind=state.stop_kind, resumable=True,
        ),
    )


def _cooldown_until_fenced(memory, harness, item_id, clock, *, rounds=1) -> int:
    attempts = 0
    while True:
        attempts += 1
        assert attempts < 1000, "auto-resume never stopped"
        state = _cooldown_state(_fresh_item(memory, item_id), "provider capacity exhausted")
        state.rounds = rounds
        assert harness._maybe_park_permanent_provider_failure(state) is None
        _settle_cooldown(memory, item_id, state)
        if state.stop_kind == "provider_fence":
            return attempts
        memory.backlog.resume_parked({"provider_cooldown"})
        clock[0] += 300.0


def test_cooldown_pauses_without_progress_stop_auto_resuming_after_three_hours(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core.autonomy import RETRY_WINDOW_SPENT_REASON
    from argus.life.supervisor import _mission_execution_runtime as runtime_module

    clock = [1_000.0]
    monkeypatch.setattr(runtime_module, "time", SimpleNamespace(time=lambda: clock[0]))
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(BacklogItem.new(title="prove the bound", objective="prove it"))
    harness = _RuntimeHarness(memory)

    attempts = _cooldown_until_fenced(memory, harness, item.id, clock)

    # One attempt every five minutes for three hours, then none.
    assert attempts == 37
    fenced = _fresh_item(memory, item.id)
    assert fenced.status == "parked"
    assert fenced.last_error.startswith(RETRY_WINDOW_SPENT_REASON)
    assert memory.backlog.resume_parked({"provider_cooldown"}) == []
