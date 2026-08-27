from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.engineer.external_work import (
    EXTERNAL_WORK_PROTOCOL_VERSION,
    ExternalWorkState,
    inspect_external_work,
    parse_external_wait_request,
    render_external_work_advisory,
    scan_external_work,
    wait_for_external_work_cadence,
)


def _write_external(root: Path, file_id: str, **overrides: object) -> Path:
    registry = root / ".argus_external_work"
    registry.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": EXTERNAL_WORK_PROTOCOL_VERSION,
        "work_id": file_id,
        "state": "running_healthy",
        "heartbeat_at": 100.0,
        "stale_after_seconds": 60.0,
        "poll_after_seconds": 30.0,
        "description": "external experiment",
        "evidence_paths": ["experiments/result.json"],
        "activity_paths": ["experiments/progress.jsonl"],
    }
    payload.update(overrides)
    path = registry / f"{file_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_canonical_external_work_supports_all_control_states(tmp_path: Path) -> None:
    for state in ExternalWorkState:
        _write_external(tmp_path, state.value, state=state.value)

    statuses = {status.work_id: status for status in scan_external_work(tmp_path, now=110)}

    assert {status.state for status in statuses.values()} == set(ExternalWorkState)
    assert statuses["running_healthy"].waitable is True
    assert all(
        not statuses[state.value].waitable
        for state in ExternalWorkState
        if state is not ExternalWorkState.RUNNING_HEALTHY
    )


def test_stale_healthy_record_downgrades_without_becoming_progress(tmp_path: Path) -> None:
    _write_external(tmp_path, "job-1", heartbeat_at=100, stale_after_seconds=10)

    status = inspect_external_work(tmp_path, "job-1", now=111)

    assert status is not None
    assert status.state is ExternalWorkState.STALLED
    assert "stale" in status.reason


def test_fresh_heartbeat_with_stale_declared_activity_needs_attention(
    tmp_path: Path,
) -> None:
    progress = tmp_path / "experiments" / "progress.jsonl"
    progress.parent.mkdir()
    progress.write_text("started\n", encoding="utf-8")
    os.utime(progress, (700, 700))
    _write_external(
        tmp_path,
        "job-1",
        heartbeat_at=990,
        stale_after_seconds=60,
        activity_stale_after_seconds=120,
        started_at=600,
    )

    status = inspect_external_work(tmp_path, "job-1", now=1000)

    assert status is not None
    assert status.state is ExternalWorkState.NEEDS_ATTENTION
    assert status.waitable is False
    assert status.activity_silence_seconds == 300
    assert "declared activity has not changed for 5m" in status.reason


def test_recent_declared_activity_keeps_fresh_heartbeat_waitable(
    tmp_path: Path,
) -> None:
    progress = tmp_path / "experiments" / "progress.jsonl"
    progress.parent.mkdir()
    progress.write_text("loading\n", encoding="utf-8")
    os.utime(progress, (980, 980))
    _write_external(
        tmp_path,
        "job-1",
        heartbeat_at=990,
        stale_after_seconds=60,
        activity_stale_after_seconds=120,
        started_at=600,
    )

    status = inspect_external_work(tmp_path, "job-1", now=1000)

    assert status is not None
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.waitable is True
    assert status.activity_silence_seconds == 20


def test_external_wait_wakes_when_activity_stalls_despite_fresh_heartbeat(
    tmp_path: Path,
) -> None:
    path = _write_external(
        tmp_path,
        "job-1",
        heartbeat_at=100,
        stale_after_seconds=60,
        activity_stale_after_seconds=10,
        poll_after_seconds=30,
        started_at=100,
    )
    progress = tmp_path / "experiments" / "progress.jsonl"
    progress.parent.mkdir(exist_ok=True)
    progress.write_text("loading\n", encoding="utf-8")
    os.utime(progress, (100, 100))
    clock = [100.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["heartbeat_at"] = clock[0]
        path.write_text(json.dumps(payload), encoding="utf-8")

    reason, waited = wait_for_external_work_cadence(
        tmp_path,
        "job-1",
        sleep=sleep,
        poll_interval=15,
        now=lambda: clock[0],
    )

    assert reason == ExternalWorkState.NEEDS_ATTENTION.value
    assert waited == 15


def test_paths_are_project_relative_and_lookup_uses_declared_id(tmp_path: Path) -> None:
    _write_external(
        tmp_path,
        "job-1",
        work_id="declared-id",
        evidence_paths=["../secret", "/etc/passwd", "results/final.json"],
    )

    status = inspect_external_work(tmp_path, "declared-id", now=110)

    assert status is not None
    assert status.evidence_paths == ("results/final.json",)
    assert inspect_external_work(tmp_path, "../../job-1", now=110) is None


def test_legacy_subagents_map_to_generic_states(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    base = {
        "mode": "supervised",
        "state": "running",
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "monitor_interval": 30,
        "worker_pid": os.getpid(),
        "heartbeat_at": 1000,
    }
    for work_id, over in {
        "healthy": {},
        "attention": {"state": "discussing"},
        "stalled": {"heartbeat_at": 1},
        "terminal": {"state": "done"},
    }.items():
        payload = {"task_id": work_id, **base, **over}
        (registry / f"{work_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    statuses = {status.work_id: status.state for status in scan_external_work(tmp_path, now=1902)}

    assert statuses == {
        "attention": ExternalWorkState.NEEDS_ATTENTION,
        "healthy": ExternalWorkState.RUNNING_HEALTHY,
        "stalled": ExternalWorkState.STALLED,
        "terminal": ExternalWorkState.TERMINAL,
    }


def test_direct_subagent_is_waitable_while_its_owner_is_alive(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    path = registry / "direct-job.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "direct-job",
                "run_id": "direct-job-run-1",
                "mode": "direct",
                "state": "running",
                "worker_pid": os.getpid(),
                "started_at": 123.0,
            }
        ),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "direct-job", now=10_000)

    assert status is not None
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.waitable is True
    assert status.run_id == "direct-job-run-1"
    assert status.started_at == 123.0


def test_direct_subagent_with_dead_owner_is_stalled(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    (registry / "direct-job.json").write_text(
        json.dumps(
            {
                "task_id": "direct-job",
                "mode": "direct",
                "state": "running",
                "worker_pid": 999_999_999,
            }
        ),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "direct-job")

    assert status is not None
    assert status.state is ExternalWorkState.STALLED
    assert status.waitable is False


def test_direct_subagent_stays_waitable_when_launcher_dies_but_child_lives(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    (registry / "direct-job.json").write_text(
        json.dumps(
            {
                "task_id": "direct-job",
                "mode": "direct",
                "state": "running",
                "worker_pid": 999_999_999,
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "direct-job")

    assert status is not None
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.waitable is True


def test_subagent_live_pid_with_mismatched_identity_is_stalled(tmp_path: Path) -> None:
    from argus_skill.core.process_identity import capture_process_identity

    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    identity = capture_process_identity(os.getpid())
    assert "start_time_ticks" in identity
    identity["start_time_ticks"] = f"{identity['start_time_ticks']}-reused"
    (registry / "direct-job.json").write_text(
        json.dumps({
            "task_id": "direct-job",
            "mode": "direct",
            "state": "running",
            "pid": os.getpid(),
            "process_identity": identity,
        }),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "direct-job")

    assert status is not None
    assert status.state is ExternalWorkState.STALLED


def test_subagent_poll_uses_published_next_check_at(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    now = 10_000.0
    (registry / "supervised-job.json").write_text(
        json.dumps({
            "task_id": "supervised-job",
            "mode": "supervised",
            "state": "running",
            "worker_pid": os.getpid(),
            "last_supervisor_health": "healthy",
            "last_supervisor_decision": "continue",
            "heartbeat_at": now,
            "monitor_interval": 120,
            "current_monitor_interval": 480,
            "next_check_at": now + 475,
        }),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "supervised-job", now=now)

    assert status is not None
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.poll_after_seconds == 475


def test_direct_subagent_exit_receipt_is_terminal(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    receipt = tmp_path / "exit-code"
    receipt.write_text("0\n", encoding="utf-8")
    (registry / "direct-job.json").write_text(
        json.dumps(
            {
                "task_id": "direct-job",
                "mode": "direct",
                "state": "running",
                "worker_pid": os.getpid(),
                "pid": os.getpid(),
                "exit_status_path": str(receipt),
            }
        ),
        encoding="utf-8",
    )

    status = inspect_external_work(tmp_path, "direct-job")

    assert status is not None
    assert status.state is ExternalWorkState.TERMINAL
    assert status.waitable is False


def test_wait_wakes_on_terminal_without_claiming_success(tmp_path: Path) -> None:
    path = _write_external(tmp_path, "job-1")
    clock = [100.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["state"] = "terminal"
        payload["outcome"] = "failed"
        path.write_text(json.dumps(payload), encoding="utf-8")

    reason, waited = wait_for_external_work_cadence(
        tmp_path,
        "job-1",
        sleep=sleep,
        poll_interval=5,
        now=lambda: clock[0],
    )

    assert reason == "terminal"
    assert waited == 5
    assert inspect_external_work(tmp_path, "job-1", now=clock[0]).outcome == "failed"


def test_advisory_and_sentinel_are_explicit_about_liveness_only(tmp_path: Path) -> None:
    _write_external(tmp_path, "job-1")

    advisory = render_external_work_advisory(tmp_path, now=110)

    assert "not scientific evidence" in advisory
    assert '"wait_for": "external_work"' in advisory
    assert "WAIT_FOR_EXTERNAL_WORK:" not in advisory
    assert parse_external_wait_request(
        'summary\n{"wait_for": "external_work", "wait_id": "job-1"}'
    ) == ("external_work", "job-1")
    assert parse_external_wait_request("WAIT_FOR_EXTERNAL_WORK: job-1") is None


def test_advisory_requires_diagnosis_for_stale_activity(tmp_path: Path) -> None:
    progress = tmp_path / "experiments" / "progress.jsonl"
    progress.parent.mkdir()
    progress.write_text("", encoding="utf-8")
    os.utime(progress, (100, 100))
    _write_external(
        tmp_path,
        "job-1",
        heartbeat_at=990,
        activity_stale_after_seconds=60,
        started_at=100,
    )

    advisory = render_external_work_advisory(tmp_path, now=1000)

    assert "needs_attention" in advisory
    assert "must not be waited on or foreground-polled" in advisory
    assert "repair, cancel, or restart" in advisory


def test_a_job_that_declares_no_activity_paths_is_still_watched(tmp_path) -> None:
    """The stall detector measured only owner-declared activity paths, and
    every live job across seven campaigns declared none, so it returned zero
    for all of them and nothing was ever judged stalled. run-01 waited four
    hours on a claim run with zero bytes on both streams at 0% CPU while a GPU
    sat idle, and it stopped only because a human looked at it.
    """
    import time

    from argus_skill.engineer.external_work import _activity_silence_seconds

    now = time.time()
    subagents = tmp_path / ".argus_subagents"
    (subagents / "quiet_logs").mkdir(parents=True)
    (subagents / "busy_logs").mkdir(parents=True)
    record = {"started_at": now - 4 * 3600}

    # Nothing written anywhere: silence runs from the start of the job.
    quiet = subagents / "quiet.json"
    assert _activity_silence_seconds(record, path=quiet, now=now) > 3 * 3600

    # A job writing to its own log is not silent, even with no declared paths.
    busy = subagents / "busy.json"
    (subagents / "busy_logs" / "stdout.log").write_text("progress\n")
    assert _activity_silence_seconds(record, path=busy, now=now) < 60

    # An empty file is not a byte written.
    (subagents / "quiet_logs" / "stdout.log").write_text("")
    assert _activity_silence_seconds(record, path=quiet, now=now) > 3 * 3600
