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
