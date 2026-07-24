from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.engineer.background_subagents import emit_subagent_cost_events
from argus_skill.engineer.external_work import (
    ExternalWorkState,
    parse_external_wait_request,
    render_external_work_advisory,
    scan_external_work,
)


def _write_record(root: Path, task_id: str, **fields: object) -> Path:
    registry = root / ".argus_subagents"
    registry.mkdir()
    record = {
        "task_id": task_id,
        "description": f"job {task_id}",
        "mode": "supervised",
        "state": "running",
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "last_supervisor_concern": "",
        "monitor_interval": 120,
        "worker_pid": os.getpid(),
    }
    record.update(fields)
    path = registry / f"{task_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_subagent_liveness_uses_unified_external_work_view(tmp_path: Path) -> None:
    _write_record(tmp_path, "train-1")

    status = scan_external_work(tmp_path)[0]

    assert status.work_id == "train-1"
    assert status.source == "subagent"
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert status.waitable


def test_subagent_attention_and_advisory_use_unified_path(tmp_path: Path) -> None:
    _write_record(tmp_path, "train-1", last_supervisor_health="diverging")

    status = scan_external_work(tmp_path)[0]
    advisory = render_external_work_advisory(tmp_path)

    assert status.state is ExternalWorkState.NEEDS_ATTENTION
    assert "train-1" in advisory
    assert "subagent" in advisory


def test_terminal_subagent_is_omitted_from_advisory(tmp_path: Path) -> None:
    _write_record(tmp_path, "train-1", state="done")

    status = scan_external_work(tmp_path)[0]

    assert status.state is ExternalWorkState.TERMINAL
    assert render_external_work_advisory(tmp_path) == ""


def test_structured_subagent_wait_request() -> None:
    request = '{"wait_for": "subagent", "wait_id": "train-1"}'
    assert parse_external_wait_request(request) == ("subagent", "train-1")
    assert parse_external_wait_request("WAIT_FOR_SUBAGENT: train-1") is None


def test_emit_subagent_cost_events_persists_delta_baseline(tmp_path: Path) -> None:
    path = _write_record(
        tmp_path,
        "train-1",
        supervisor_usage_model="gpt-5.5",
        supervisor_input_tokens=120,
        supervisor_cached_input_tokens=15,
        supervisor_output_tokens=30,
        supervisor_reasoning_output_tokens=6,
    )
    events: list[dict[str, object]] = []

    emit_subagent_cost_events(tmp_path, events.append)
    emit_subagent_cost_events(tmp_path, events.append)

    assert len(events) == 1
    assert events[0]["input_tokens"] == 120
    assert events[0]["output_tokens"] == 30
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["supervisor_cost_folded_totals"]["input_tokens"] == 120
