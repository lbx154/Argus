"""Resubmitting a task id must not erase an earlier run's logs or record.

Background jobs are keyed by task id, and engineers routinely resubmit the
same id with different settings (one arm of a sweep, a retry with a changed
flag). When stdout/stderr and the job record lived at one per-task path, the
second submission truncated the first's evidence; a reviewer then had no way
to see what the earlier run did and asked for a rerun just to recover it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from argus.tools import subagent as _sub
from argus.tools.subagent import _direct_run, _registry, _reporting


def _submit(task_id: str, run_id: str, message: str, alerts: list) -> None:
    _sub._write_task(task_id, {
        "task_id": task_id,
        "run_id": run_id,
        "state": "queued",
        "settings": {"message": message},
    })
    _direct_run._run_direct(
        task_id,
        f'{sys.executable} -c "print(\'{message}\')"',
        f"arm {message}",
        timeout=60,
        cwd=str(Path.cwd()),
    )


def test_resubmitted_task_keeps_each_runs_logs_and_record(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _direct_run, "experiment_launch_preflight", lambda **_k: (False, ""),
    )
    monkeypatch.setattr(
        _direct_run, "release_experiment_launch_claim", lambda **_k: None,
    )
    monkeypatch.setattr(_direct_run, "acquire_for_task", lambda *a, **k: None)
    alerts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        _direct_run,
        "_alert_engineer",
        lambda task_id, event, data: alerts.append((event, dict(data))) or "",
    )

    _submit("arm", "arm-run-1", "first-settings", alerts)
    _submit("arm", "arm-run-2", "second-settings", alerts)

    first, second = alerts[0][1], alerts[1][1]
    assert first["stdout_log"] != second["stdout_log"]
    assert "first-settings" in Path(first["stdout_log"]).read_text()
    assert "second-settings" in Path(second["stdout_log"]).read_text()

    # Existing readers of the stable per-task path see the latest run.
    stable = _sub._task_log_dir("arm") / "stdout.log"
    assert "second-settings" in stable.read_text()

    # The first run's own record survives the resubmission.
    run_record = _registry._run_record_path("arm", "arm-run-1")
    record_one = json.loads(run_record.read_text())
    assert record_one["run_id"] == "arm-run-1"
    assert record_one["settings"] == {"message": "first-settings"}

    # The report sent to the reviewer cites the run's own log and record.
    report = _reporting._build_report("arm", "COMPLETED", first)
    assert first["stdout_log"] in report
    assert str(run_record) in report
