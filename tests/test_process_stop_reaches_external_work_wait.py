"""A stop request must reach a round that is waiting on external work.

The daemon stops between missions. A round that yields to external work stays
inside its mission for as long as that work keeps running, so before this the
wait loop could outlive any signal: the daemon logged "requesting stop" and
then sat in `time.sleep` until it was killed.
"""

from __future__ import annotations

import json
import time

import pytest

from argus_skill.core import process_stop
from argus_skill.engineer.external_work import (
    EXTERNAL_WORK_PROTOCOL_VERSION,
    wait_for_external_work_cadence,
)


@pytest.fixture(autouse=True)
def _clean_flag():
    process_stop.clear_stop()
    yield
    process_stop.clear_stop()


def test_flag_starts_clear_and_records_a_request() -> None:
    assert not process_stop.stop_requested()
    process_stop.request_stop()
    assert process_stop.stop_requested()
    process_stop.clear_stop()
    assert not process_stop.stop_requested()


def test_cadence_wait_leaves_once_a_stop_is_requested(tmp_path) -> None:
    """The wait sleeps in poll-sized chunks, so it must not serve a whole
    cadence after the process has been asked to stop."""
    registry = tmp_path / ".argus_external_work"
    registry.mkdir()
    (registry / "job.json").write_text(
        json.dumps({
            "version": EXTERNAL_WORK_PROTOCOL_VERSION,
            "work_id": "job",
            "state": "running_healthy",
            "heartbeat_at": 100.0,
            "stale_after_seconds": 60.0,
            "poll_after_seconds": 30.0,
            "description": "external experiment",
        }),
        encoding="utf-8",
    )

    slept: list[float] = []

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        process_stop.request_stop()

    reason, waited = wait_for_external_work_cadence(
        tmp_path,
        "job",
        sleep=_sleep,
        poll_interval=1.0,
        now=lambda: 100.0,
    )

    assert reason == "stop_requested"
    assert len(slept) == 1
    assert waited == pytest.approx(1.0)


def test_daemon_stop_request_sets_the_process_flag() -> None:
    """The signal handler's cooperative stop is what long waits read."""
    from argus_skill.daemon import life_worker

    source = life_worker.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    marker = "process_stop.request_stop()"
    assert marker in text
    assert text.index(marker) < text.index("self._stop.set()")


def test_round_wait_loop_cannot_spin_past_a_stop(tmp_path, monkeypatch) -> None:
    """A healthy long job keeps the cadence elapsing. Without a stop check the
    loop never returns, so the mission never ends and the signal never lands."""
    from argus_skill.engineer import round_waits, runner
    from argus_skill.engineer.round_state import RoundLoopState

    calls: list[int] = []

    def _always_elapsed(**_kwargs):
        calls.append(1)
        if len(calls) > 50:
            raise AssertionError("wait loop ignored the stop request")
        if len(calls) == 3:
            process_stop.request_stop()
        return ("cadence_elapsed", 30.0)

    monkeypatch.setattr(runner, "_run_external_work_wait", _always_elapsed)

    registry = tmp_path / ".argus_external_work"
    registry.mkdir()
    (registry / "job.json").write_text(
        json.dumps({
            "version": EXTERNAL_WORK_PROTOCOL_VERSION,
            "work_id": "job",
            "state": "running_healthy",
            "heartbeat_at": time.time(),
            "stale_after_seconds": 60.0,
            "poll_after_seconds": 30.0,
            "description": "external experiment",
        }),
        encoding="utf-8",
    )

    class _Config:
        max_rounds = 32
        background_subagent_advisory = True

    holder = round_waits.RoundWaitsMixin()
    holder._handle_agent_driven_wait(
        round_index=3,
        supervised_config=_Config(),
        raw_engineer_message='{"wait_for": "external_work", "wait_id": "job"}',
        workdir=tmp_path,
        state=RoundLoopState(),
        on_event=None,
    )

    assert calls, "the wait never ran"
    assert len(calls) == 3


def test_a_long_wait_says_how_long_it_has_been_waiting(monkeypatch, tmp_path) -> None:
    """Every cadence tick emitted the same two lines, so an eighteen-hour wait
    and a two-minute one looked identical in the timeline: five hundred
    "resumed after 120s" lines and nothing saying how long this had gone on.
    One campaign held four GPUs through such a wait across five rounds and the
    cost was only visible by counting the events."""
    from argus_skill.engineer import round_signals

    monkeypatch.setattr(
        round_signals,
        "wait_for_external_work_cadence",
        lambda _workdir, _work_id: ("cadence_elapsed", 120.0),
    )
    events: list[dict] = []
    round_signals._run_external_work_wait(
        workdir=tmp_path,
        work_id="phase2-imagenet-run",
        round_index=1,
        round_max=32,
        on_event=events.append,
        waited_total_s=22_320.0,
    )

    done = events[-1]
    assert done["waited_total_s"] == 22_440.0
    assert "6h14m on phase2-imagenet-run" in done["text"]
    # The slice is still there; it is the total that was missing.
    assert "resumed after 120s (cadence_elapsed)" in done["text"]
    assert events[0]["waited_total_s"] == 22_320.0
