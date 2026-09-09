from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from argus_skill.core.models import ReviewDecision, RunnerResult
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


def test_job_launched_after_prompt_assembly_can_yield_without_a_paper_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.engineer import runner
    from argus_skill.roles.prompts.engineer import build_mission_prompt

    registry = tmp_path / ".argus_subagents"
    record_path = registry / "new-panel.json"

    class Engineer:
        backend = "test"

        def run_exec(self, *, prompt, **_kwargs):
            # No live-job advisory existed when this first prompt was built.
            assert not registry.exists()
            assert '{"wait_for":"subagent","wait_id":"<task-id>"}' in prompt
            assert "## External work status" not in prompt
            registry.mkdir()
            record_path.write_text(json.dumps({
                "task_id": "new-panel", "state": "running", "mode": "direct",
                "pid": os.getpid(),
            }), encoding="utf-8")
            return RunnerResult(exit_code=0, agent_messages=[
                'The repaired panel is running; its measurements are pending.\n'
                '{"wait_for":"subagent","wait_id":"new-panel"}'
            ])

    class ReviewerMustNotRun:
        def evaluate(self, **_kwargs):
            raise AssertionError("a pending panel must yield before paper review")

    monkeypatch.setattr(
        runner, "_run_external_work_wait",
        lambda **_kwargs: ("cadence_elapsed", 30.0),
    )
    engine = SupervisedEngineer(
        engineer_runner=Engineer(), reviewer=ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )
    status, rounds, message, _reason, _thread = engine.run(
        objective="validate a repaired panel before reviewing the paper",
        engineer_prompt_builder=lambda next_action, include_static=True: build_mission_prompt(
            task="Validate the repaired panel.", skill_text="", next_action=next_action,
            include_static=include_static,
        ),
        supervised_config=SupervisedConfig(max_rounds=2),
        workdir=tmp_path,
    )
    assert status == "paused_external_work"
    assert not rounds
    assert "measurements are pending" in message
    assert json.loads(record_path.read_text())["state"] == "running"


@pytest.mark.parametrize("job_state", ["done", "error"])
@pytest.mark.parametrize("source", ["subagent", "external_work"])
def test_job_finished_before_wait_handoff_returns_to_engineer_before_review(
    tmp_path: Path, job_state: str, source: str,
) -> None:
    registry = tmp_path / (".argus_subagents" if source == "subagent" else ".argus_external_work")
    registry.mkdir()
    record = {
        "task_id": "analysis", "run_id": "analysis-run-1",
        "state": job_state, "mode": "direct", "pid": os.getpid(),
    } if source == "subagent" else {
        "version": 1, "work_id": "analysis", "state": "terminal",
        "outcome": job_state, "heartbeat_at": time.time(),
    }
    (registry / "analysis.json").write_text(json.dumps(record))
    manuscript = tmp_path / "manuscript.txt"
    manuscript.write_text("results have not been processed")
    reviewed: list[str] = []

    class Engineer:
        backend = "test"
        calls = 0

        def run_exec(self, *, prompt, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return RunnerResult(exit_code=0, agent_messages=[
                    'The analysis must be handled before review.\n'
                    + json.dumps({"wait_for": source, "wait_id": "analysis"})
                ])
            assert "External work follow-up" in prompt
            assert "`analysis`" in prompt
            if source == "subagent":
                assert "analysis-run-1" in prompt
            manuscript.write_text(f"handled existing {job_state} result")
            return RunnerResult(exit_code=0, agent_messages=["Existing result handled."])

    class Reviewer:
        def evaluate(self, **_kwargs):
            reviewed.append(manuscript.read_text())
            return ReviewDecision(
                status="done", reason="current result was handled", next_action=None,
            )

    engineer = Engineer()
    engine = SupervisedEngineer(
        engineer_runner=engineer, reviewer=Reviewer(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )
    status, _rounds, _message, _reason, _thread = engine.run(
        objective="handle the requested calculation before review",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(max_rounds=3), workdir=tmp_path,
    )

    assert status == "done"
    assert engineer.calls == 2
    assert reviewed == [f"handled existing {job_state} result"]


def test_finished_wait_continuation_is_once_per_run_not_an_empty_loop(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    path = registry / "analysis.json"
    record = {"task_id": "analysis", "run_id": "run-1", "state": "done", "mode": "direct"}
    path.write_text(json.dumps(record))
    state = RoundLoopState(
        backend_failure_streak=2, backend_failure_signature="old provider failure",
        backend_failure_same_cause_streak=2,
    )
    holder = RoundWaitsMixin()

    def observe():
        return holder._handle_agent_driven_wait(
            round_index=1, supervised_config=SupervisedConfig(),
            raw_engineer_message='{"wait_for":"subagent","wait_id":"analysis"}',
            workdir=tmp_path, state=state, on_event=None,
        )

    assert observe().action == "continue_loop"
    assert state.backend_failure_streak == 0
    assert state.backend_failure_signature == ""
    assert state.backend_failure_same_cause_streak == 0
    assert observe().action == "proceed"
    record["run_id"] = "run-2"
    path.write_text(json.dumps(record))
    assert observe().action == "continue_loop"


def test_finished_wait_honors_shutdown_before_starting_another_turn(tmp_path: Path) -> None:
    from argus_skill.core import process_stop

    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    (registry / "analysis.json").write_text(json.dumps({
        "task_id": "analysis", "run_id": "run-1", "state": "done", "mode": "direct",
    }))
    process_stop.request_stop()
    try:
        result = RoundWaitsMixin()._handle_agent_driven_wait(
            round_index=1, supervised_config=SupervisedConfig(),
            raw_engineer_message='{"wait_for":"subagent","wait_id":"analysis"}',
            workdir=tmp_path, state=RoundLoopState(), on_event=None,
        )
    finally:
        process_stop.clear_stop()

    assert result.action == "return"
    assert result.terminal[0] == "paused_daemon_shutdown"


def test_job_observed_finishing_during_wait_is_not_consumed_twice(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.engineer import runner

    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    path = registry / "analysis.json"
    record = {"task_id": "analysis", "run_id": "run-1", "state": "running",
              "mode": "direct", "pid": os.getpid()}
    path.write_text(json.dumps(record))

    def finish(**_kwargs):
        record["state"] = "done"
        path.write_text(json.dumps(record))
        return "state_changed", 1.0

    monkeypatch.setattr(runner, "_run_external_work_wait", finish)
    holder, state = RoundWaitsMixin(), RoundLoopState()

    def observe():
        return holder._handle_agent_driven_wait(
            round_index=1, supervised_config=SupervisedConfig(),
            raw_engineer_message='{"wait_for":"subagent","wait_id":"analysis"}',
            workdir=tmp_path, state=state, on_event=None,
        )

    assert observe().action == "continue_loop"
    assert "External work follow-up" in state.pending_external_work_followup
    state.pending_external_work_followup = ""
    assert observe().action == "proceed"
    assert state.pending_external_work_followup == ""


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
