from __future__ import annotations

import json
from pathlib import Path

from argus.skills.stage_machine import current_stage


def _write_state(root: Path, stage: str) -> Path:
    path = root / ".argus" / "PIPELINE_STATE.json"
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


def test_kernel_stage_alias_read_does_not_rewrite_legacy_state(tmp_path: Path) -> None:
    path = _write_state(tmp_path, "profiling")

    assert current_stage(tmp_path) == "optimize"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["current_stage"] == "profiling"
