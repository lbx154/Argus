from __future__ import annotations

import json
from pathlib import Path

from argus_skill.engineer.long_job_policy import (
    classify_long_job_command,
    find_unmanaged_long_jobs,
)


def test_command_classifier_requires_durable_owner() -> None:
    assert classify_long_job_command(
        "/shared/run_on_free_gpu.sh python train.py"
    ) == "unmanaged_long_job"
    assert classify_long_job_command(
        "python -m argus_skill.tools.subagent submit --task-id train "
        "--command '/shared/run_on_free_gpu.sh python train.py'"
    ) == "managed"


def test_trace_scanner_distinguishes_raw_launch_and_managed_submit(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    rows = []
    for command in (
        "/shared/run_on_free_gpu.sh python train.py",
        "python -m argus_skill.tools.subagent submit --task-id train "
        "--command '/shared/run_on_free_gpu.sh python train.py'",
    ):
        nested = {
            "type": "tool.execution_start",
            "timestamp": "2026-07-19T00:00:00Z",
            "data": {"toolName": "bash", "arguments": {"command": command}},
        }
        rows.append({
            "type": "agent.io.stream",
            "call_id": "call-1",
            "ts": 10.0,
            "line": json.dumps(nested),
        })
    nested_wait = {
        "type": "tool.execution_start",
        "timestamp": "2026-07-19T00:01:00Z",
        "data": {"toolName": "read_bash", "arguments": {"shellId": "x", "delay": 600}},
    }
    rows.append({
        "type": "agent.io.stream",
        "call_id": "call-1",
        "ts": 11.0,
        "line": json.dumps(nested_wait),
    })
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    findings = find_unmanaged_long_jobs(path, call_id="call-1", since=9.0)
    assert [row["classification"] for row in findings] == [
        "unmanaged_long_job",
        "unmanaged_busy_wait",
    ]


def test_engineer_role_requires_subagent_for_long_jobs() -> None:
    role = Path(
        "argus_skill/builtin_skills/engineer/argus-engineer-role.md"
    ).read_text()
    assert "longer than two minutes" in role
    assert "WAIT_FOR_SUBAGENT" in role
