from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus_skill.skills import run_contract
from argus_skill.tools.subagent import (
    _direct_run,
    _discuss_run,
    _supervised_preflight,
    _supervised_run,
)


def _raise_backend(*_args: object, **_kwargs: object) -> object:
    raise TimeoutError("dead relay")


def _prepare_supervised_run(
    monkeypatch: pytest.MonkeyPatch,
    proc: object,
) -> None:
    monkeypatch.setattr(
        _supervised_run,
        "experiment_launch_preflight",
        lambda **_kwargs: (False, ""),
    )
    monkeypatch.setattr(
        _supervised_run,
        "_launch_durable_command",
        lambda **_kwargs: proc,
    )
    monkeypatch.setattr(
        _supervised_run,
        "_persist_experiment_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(_supervised_run, "_tail_file", lambda *_args: "")


def test_health_check_backend_failure_has_distinct_health(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_supervised_run, "_run_supervisor_with_usage", _raise_backend)

    result = _supervised_run._supervisor_check_with_usage(
        "training",
        "python train.py",
        "long GPU run",
        tmp_path / "stdout.log",
        tmp_path / "stderr.log",
        10.0,
        1,
        "model",
        str(tmp_path),
    )

    assert result == _supervised_run.SupervisorCheck(
        decision="continue",
        health="supervisor_unavailable",
        concern="",
        thread_id=None,
        usage=(0, 0, 0, 0),
        error="TimeoutError: dead relay",
    )
    assert _supervised_run._norm_health("supervisor-unavailable") == (
        "supervisor_unavailable"
    )


def test_contract_interlock_distinguishes_skip_from_malformed_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def framework_bug(**_kwargs: object) -> tuple[bool, str]:
        raise RuntimeError("checker bug")

    monkeypatch.setattr(run_contract, "check_full_run_launch", framework_bug)
    reject, concern, status = _direct_run._run_contract_preflight(
        "python train.py --run-contract frozen.json",
        str(tmp_path),
    )
    assert (reject, concern, status) == (False, "", "skipped")

    def malformed(**_kwargs: object) -> tuple[bool, str]:
        raise ValueError("invalid schema")

    monkeypatch.setattr(run_contract, "check_full_run_launch", malformed)
    reject, concern, status = _direct_run._run_contract_preflight(
        "python train.py --run-contract frozen.json",
        str(tmp_path),
    )
    assert reject is True
    assert status == ""
    assert str(tmp_path / "frozen.json") in concern
    assert "ValueError: invalid schema" in concern


def test_preflight_backend_failure_visible_but_nonbool_reply_still_fails_soft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        _supervised_preflight,
        "_run_supervisor_with_usage",
        _raise_backend,
    )
    assert _supervised_preflight._supervisor_preflight_with_usage(
        "training",
        "python train.py --method grpo",
        "long GPU run",
        "model",
        str(tmp_path),
    ) == (False, "", (0, 0, 0, 0), "unavailable")

    monkeypatch.setattr(
        _supervised_preflight,
        "_run_supervisor_with_usage",
        lambda *_args, **_kwargs: (
            ['{"reject": "true", "concern": "num_generations=1 -> 8"}'],
            None,
            (7, 0, 3, 0),
        ),
    )
    assert _supervised_preflight._supervisor_preflight_with_usage(
        "training",
        "python train.py --method grpo",
        "long GPU run",
        "model",
        str(tmp_path),
    ) == (False, "", (7, 0, 3, 0), "")
    assert _supervised_preflight._next_monitor_interval(
        "supervisor_unavailable",
        900,
        120,
    ) == 120


def test_discussion_backend_failure_forces_explicit_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_discuss_run, "_run_supervisor_with_usage", _raise_backend)

    resolved, message, thread_id, usage = (
        _discuss_run._supervisor_discuss_with_usage(
            "training",
            {"description": "long GPU run"},
            "model",
            str(tmp_path),
            "thread-1",
        )
    )

    assert resolved is True
    assert thread_id == "thread-1"
    assert usage == (0, 0, 0, 0)
    assert "TimeoutError: dead relay" in message
    assert "supervisor_log" in message
    assert "rather than waiting" in message


def test_prelaunch_guard_statuses_reach_task_record_and_supervisor_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FinishedProc:
        pid = 4100
        returncode = 0

        def wait(self, timeout: int | None = None) -> int:
            return 0

    monkeypatch.chdir(tmp_path)
    _prepare_supervised_run(monkeypatch, FinishedProc())
    monkeypatch.setattr(
        _supervised_run,
        "_run_contract_preflight",
        lambda *_args: (False, "", "skipped"),
    )
    monkeypatch.setattr(
        _supervised_run,
        "_supervisor_preflight_with_usage",
        lambda *_args: (False, "", (0, 0, 0, 0), "unavailable"),
    )
    monkeypatch.setattr(_supervised_run, "_alert_engineer", lambda *_args: "report")

    _supervised_run._run_supervised(
        "training",
        "python train.py --scale full --method grpo --num-generations 2",
        "long GPU run",
        timeout=100,
        monitor_interval=1,
        model="model",
        cwd=str(tmp_path),
    )

    task = _supervised_run._read_task("training")
    assert task is not None
    assert task["provenance_interlock"] == "skipped"
    assert task["preflight"] == "unavailable"
    rows = [
        json.loads(line)
        for line in Path(task["supervisor_log"]).read_text().splitlines()
    ]
    assert {row.get("provenance_interlock") for row in rows} >= {"skipped"}
    assert {row.get("preflight") for row in rows} >= {"unavailable"}


def test_consecutive_monitor_failures_alert_once_and_remain_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FourChecksThenDone:
        pid = 4200
        returncode = 0

        def __init__(self) -> None:
            self.calls = 0

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls <= 4:
                raise subprocess.TimeoutExpired("training", timeout)
            return 0

    monkeypatch.chdir(tmp_path)
    _prepare_supervised_run(monkeypatch, FourChecksThenDone())
    monkeypatch.setattr(_supervised_run, "_run_supervisor_with_usage", _raise_backend)
    alerts: list[str] = []

    def capture_alert(_task_id: str, event: str, _task: dict[str, object]) -> str:
        alerts.append(event)
        return "report"

    monkeypatch.setattr(_supervised_run, "_alert_engineer", capture_alert)

    _supervised_run._run_supervised(
        "training",
        "python train.py",
        "long GPU run",
        timeout=100,
        monitor_interval=1,
        model="model",
        cwd=str(tmp_path),
        preflight=False,
    )

    assert alerts.count("SUPERVISION-UNAVAILABLE") == 1
    task = _supervised_run._read_task("training")
    assert task is not None
    assert task["supervision"] == "unavailable"
    assert task["last_supervisor_health"] == "supervisor_unavailable"
    rows = [
        json.loads(line)
        for line in Path(task["supervisor_log"]).read_text().splitlines()
    ]
    unavailable = [
        row for row in rows if row.get("health") == "supervisor_unavailable"
    ]
    assert len(unavailable) == 4
    assert all(row["supervisor_error"] == "TimeoutError: dead relay" for row in unavailable)


def test_available_monitor_check_resets_failure_streak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FiveChecksThenDone:
        pid = 4300
        returncode = 0

        def __init__(self) -> None:
            self.calls = 0

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls <= 5:
                raise subprocess.TimeoutExpired("training", timeout)
            return 0

    monkeypatch.chdir(tmp_path)
    _prepare_supervised_run(monkeypatch, FiveChecksThenDone())
    health = iter([
        "supervisor_unavailable",
        "supervisor_unavailable",
        "healthy",
        "supervisor_unavailable",
        "supervisor_unavailable",
    ])
    monkeypatch.setattr(
        _supervised_run,
        "_supervisor_check_with_usage",
        lambda *_args, **_kwargs: _supervised_run.SupervisorCheck(
            decision="continue",
            health=next(health),
            concern="",
            thread_id=None,
            usage=(0, 0, 0, 0),
            error=None,
        ),
    )
    alerts: list[str] = []
    monkeypatch.setattr(
        _supervised_run,
        "_alert_engineer",
        lambda _task_id, event, _task: alerts.append(event) or "report",
    )

    _supervised_run._run_supervised(
        "training",
        "python train.py",
        "long GPU run",
        timeout=100,
        monitor_interval=1,
        model="model",
        cwd=str(tmp_path),
        preflight=False,
    )

    assert "SUPERVISION-UNAVAILABLE" not in alerts
    task = _supervised_run._read_task("training")
    assert task is not None
    assert "supervision" not in task


def test_confirmation_backend_failure_records_error_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def fail_confirmation(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                [
                    json.dumps({
                        "decision": "continue",
                        "health": "degrading",
                        "concern": "learning_rate=1e-3 is unstable; try 1e-4",
                    })
                ],
                "thread-1",
                (4, 0, 2, 0),
            )
        raise TimeoutError("dead confirmation relay")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _supervised_run,
        "_run_supervisor_with_usage",
        fail_confirmation,
    )
    monkeypatch.setattr(_supervised_run, "_tail_file", lambda *_args: "")
    supervisor_log = tmp_path / "supervisor.jsonl"
    with (tmp_path / "stdout.log").open("w") as out, (
        tmp_path / "stderr.log"
    ).open("w") as err:
        result = _supervised_run._supervised_do_one_check(
            task_id="training",
            command="python train.py --learning-rate 1e-3",
            description="long GPU run",
            out=out,
            err=err,
            check_number=1,
            model="model",
            cwd=str(tmp_path),
            resolved_run_dir=None,
            start_time=0.0,
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            supervisor_log=supervisor_log,
            supervisor_thread_id=None,
            supervisor_usage_totals=(0, 0, 0, 0),
        )

    assert result[2] == "supervisor_unavailable"
    confirmation = json.loads(supervisor_log.read_text().splitlines()[1])
    assert confirmation["health"] == "supervisor_unavailable"
    assert confirmation["supervisor_error"] == (
        "TimeoutError: dead confirmation relay"
    )
