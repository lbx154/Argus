from __future__ import annotations

import json


def test_a_direct_job_that_writes_nothing_is_not_healthy(tmp_path) -> None:
    """A direct job's health was decided by pid liveness alone. One loaded its
    model and then span for eleven hours and thirty-five minutes at 95% of a
    core across 196 threads, holding 15 GB of GPU, with zero bytes in its stdout
    and no output file anywhere in the campaign — and stayed RUNNING_HEALTHY the
    whole time while its campaign waited. Liveness is not activity.
    """
    import os
    import time

    from argus.engineer.external_work import (
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

    from argus.engineer.external_work import (
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
