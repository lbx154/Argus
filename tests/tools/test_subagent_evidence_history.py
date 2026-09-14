from __future__ import annotations

import json

from argus.tools.subagent._registry import (
    EXPERIMENT_HISTORY_REL,
    _append_experiment_history,
    append_experiment_correction,
)


def test_experiment_correction_is_additive_and_keeps_original_failure(tmp_path) -> None:
    original = {
        "run_id": "run-1",
        "task_id": "task-1",
        "event": "FAILED",
        "state": "error",
        "exit_code": 1,
    }
    _append_experiment_history(str(tmp_path), original)

    correction = append_experiment_correction(
        str(tmp_path),
        run_id="run-1",
        correction_id="run-1-terminal-correction",
        relation="reclassifies",
        reason="The workload completed before its external teardown guard failed.",
        evidence_refs=["checkpoint/step-100", "receipts/restore.json"],
        details={"workload_completed": True, "wrapper_exit_code": 1},
    )

    path = tmp_path / EXPERIMENT_HISTORY_REL
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["state"] == "error"
    assert rows[0]["exit_code"] == 1
    assert rows[1] == correction
    assert rows[1]["target_record_id"] == "run-1"
    assert rows[1]["details"]["wrapper_exit_code"] == 1
