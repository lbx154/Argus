from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.external_work import parse_external_wait_request
from argus_skill.engineer.round_config import EngineerConfig, SupervisedConfig
from argus_skill.engineer.round_state import RoundLoopState
from argus_skill.engineer.round_waits import RoundWaitsMixin
from argus_skill.engineer.runner import SupervisedEngineer
from argus_skill.reviewer import ReviewerConfig


def test_subagent_wait_uses_structured_request() -> None:
    assert parse_external_wait_request(
        '{"wait_for": "subagent", "wait_id": "task-123"}'
    ) == ("subagent", "task-123")


def test_external_work_wait_uses_structured_request() -> None:
    assert parse_external_wait_request(
        '{"wait_for": "external_work", "wait_id": "work-123"}'
    ) == ("external_work", "work-123")
    assert parse_external_wait_request(
        'ARGUS_ROLE_DECISION={"role":"engineer"} '
        '{"wait_for":"external_work","wait_id":"work-123"}'
    ) == ("external_work", "work-123")


def test_incomplete_json_is_not_a_wait_request() -> None:
    assert parse_external_wait_request('"wait_for": "subagent"') is None


def test_healthy_subagent_wait_releases_the_mission_after_one_cadence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    (registry / "task-123.json").write_text(json.dumps({
        "task_id": "task-123",
        "state": "running",
        "mode": "direct",
        "pid": os.getpid(),
    }), encoding="utf-8")
    calls: list[str] = []

    def wait_once(**kwargs):
        calls.append(kwargs["work_id"])
        return ("cadence_elapsed", 120.0)

    from argus_skill.engineer import runner

    monkeypatch.setattr(runner, "_run_external_work_wait", wait_once)
    state = RoundLoopState()
    progress_at = state.last_decision_progress_at

    control = RoundWaitsMixin()._handle_agent_driven_wait(
        round_index=4,
        supervised_config=SupervisedConfig(max_rounds=4),
        raw_engineer_message=(
            '{"wait_for": "subagent", "wait_id": "task-123"}'
        ),
        workdir=tmp_path,
        state=state,
        on_event=None,
    )

    assert control.action == "return"
    assert control.terminal is not None
    assert control.terminal[0] == "paused_external_work"
    assert calls == ["task-123"]
    assert state.last_decision_progress_at == progress_at + 120.0


def test_wait_uses_the_real_last_message_when_a_process_decision_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Engineer:
        backend = "test"

        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                agent_messages=[
                    'work prepared\n{"wait_for":"external_work","wait_id":"job-1"}'
                ],
                role_decisions=[{
                    "role": "engineer",
                    "payload": {
                        "status": "done",
                        "result": "material result and decisive check",
                        "next_owner": "reviewer",
                    },
                }],
            )

    class ReviewerMustNotRun:
        def evaluate(self, **_kwargs):
            raise AssertionError("a healthy external wait must release before review")

    registry = tmp_path / ".argus_external_work"
    registry.mkdir()
    (registry / "job-1.json").write_text(json.dumps({
        "version": 1,
        "work_id": "job-1",
        "state": "running_healthy",
        "heartbeat_at": time.time(),
        "stale_after_seconds": 300,
        "poll_after_seconds": 30,
        "description": "benchmark",
    }), encoding="utf-8")
    from argus_skill.engineer import runner

    monkeypatch.setattr(
        runner,
        "_run_external_work_wait",
        lambda **_kwargs: ("cadence_elapsed", 30.0),
    )
    engine = SupervisedEngineer(
        engineer_runner=Engineer(),
        reviewer=ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )

    status, _rounds, message, _reason, _thread = engine.run(
        objective="launch benchmark and continue independently",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(max_rounds=2),
        workdir=tmp_path,
    )

    assert status == "paused_external_work"
    assert '"wait_id":"job-1"' in message


def test_a_direct_job_that_writes_nothing_is_not_healthy(tmp_path) -> None:
    """A direct job's health was decided by pid liveness alone. One loaded its
    model and then span for eleven hours and thirty-five minutes at 95% of a
    core across 196 threads, holding 15 GB of GPU, with zero bytes in its stdout
    and no output file anywhere in the campaign — and stayed RUNNING_HEALTHY the
    whole time while its campaign waited. Liveness is not activity.
    """
    import os
    import time

    from argus_skill.engineer.external_work import (
        ExternalWorkState,
        scan_external_work,
    )

    registry = tmp_path / ".argus_subagents"
    logs = registry / "spinner_logs"
    logs.mkdir(parents=True)
    (logs / "stdout.log").write_text("", encoding="utf-8")
    (registry / "spinner.json").write_text(
        json.dumps({
            "task_id": "spinner",
            "state": "running",
            "mode": "direct",
            "pid": os.getpid(),
            "worker_pid": os.getpid(),
        }),
        encoding="utf-8",
    )

    def _status(silent_seconds: float):
        stale = time.time() - silent_seconds
        os.utime(logs / "stdout.log", (stale, stale))
        return next(
            s for s in scan_external_work(tmp_path) if s.work_id == "spinner"
        )

    # Quiet for a few minutes is normal work, not a problem.
    working = _status(60)
    assert working.state is ExternalWorkState.RUNNING_HEALTHY
    assert working.waitable is True

    # Quiet work remains healthy while its recorded process identity is alive.
    spinning = _status(4 * 3600)
    assert spinning.state is ExternalWorkState.RUNNING_HEALTHY
    assert spinning.waitable is True


def test_a_job_that_writes_its_results_elsewhere_is_not_accused(tmp_path) -> None:
    """The first version of the silence check flagged a job that had produced
    511 files in two hours, because it writes into results/ and its stderr was
    two hours old. That job's mission would then have lost the protection the
    stall guard gives healthy work, on the night the papers were due. A job that
    declared no evidence paths and has written a log at some point is unknown,
    not silent; only logs that were never written at all mean nobody can watch.
    """
    import os
    import time

    from argus_skill.engineer.external_work import (
        ExternalWorkState,
        scan_external_work,
    )

    registry = tmp_path / ".argus_subagents"
    logs = registry / "busy_logs"
    logs.mkdir(parents=True)
    stderr = logs / "stderr.log"
    stderr.write_text("loading model\n", encoding="utf-8")
    stale = time.time() - 4 * 3600
    os.utime(stderr, (stale, stale))
    (registry / "busy.json").write_text(
        json.dumps({
            "task_id": "busy",
            "state": "running",
            "mode": "direct",
            "pid": os.getpid(),
            "worker_pid": os.getpid(),
        }),
        encoding="utf-8",
    )

    status = next(
        s for s in scan_external_work(tmp_path) if s.work_id == "busy"
    )
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.waitable is True
