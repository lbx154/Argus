from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.stage_machine import current_stage, rollback_stage


def _write_state(root: Path, stage: str) -> Path:
    path = root / "research" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "vertical": "kernel_engineering",
            "workflow_mode": "staged",
            "current_stage": stage,
            "stages": {
                "baseline": {"status": "done"},
                "profiling": {"status": "in_progress"},
                "optimize": {"status": "in_progress"},
            },
        }),
        encoding="utf-8",
    )
    return path


def test_kernel_profiling_alias_reads_as_optimize(tmp_path: Path) -> None:
    _write_state(tmp_path, "profiling")

    assert current_stage(tmp_path) == "optimize"


def test_manager_can_rollback_from_kernel_stage_alias(tmp_path: Path) -> None:
    path = _write_state(tmp_path, "profiling")

    rollback_stage(
        tmp_path,
        target_stage="baseline",
        reason="baseline certification required",
        rolled_back_by="manager",
    )

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["current_stage"] == "baseline"
    assert state["stage_history"][-1]["from_stage"] == "optimize"
