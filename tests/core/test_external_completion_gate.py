from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.external_completion_gate import external_completion_gate_issue
from argus_skill.manager.stage_decider import final_stage_completion_decision


class _DoneReview:
    status = "done"


def test_external_completion_gate_blocks_until_exact_true(tmp_path: Path) -> None:
    spec = "MLE_MEDAL_GATE.json:satisfied"

    assert "missing" in external_completion_gate_issue(tmp_path, spec=spec)
    (tmp_path / "MLE_MEDAL_GATE.json").write_text(
        json.dumps({"satisfied": False}), encoding="utf-8"
    )
    assert "not satisfied" in external_completion_gate_issue(tmp_path, spec=spec)
    (tmp_path / "MLE_MEDAL_GATE.json").write_text(
        json.dumps({"satisfied": True}), encoding="utf-8"
    )
    assert external_completion_gate_issue(tmp_path, spec=spec) == ""


def test_external_completion_gate_rejects_unsafe_path(tmp_path: Path) -> None:
    assert "unsafe path" in external_completion_gate_issue(
        tmp_path, spec="../private.json:satisfied"
    )


def test_final_stage_certificate_cannot_override_external_gate() -> None:
    blocked = final_stage_completion_decision(
        _DoneReview(),
        current_stage="report",
        stage_order=["setup", "report"],
        vertical="speedrun",
        mission_scope="bounded",
        completion_blocker="external completion gate is not satisfied",
    )
    allowed = final_stage_completion_decision(
        _DoneReview(),
        current_stage="report",
        stage_order=["setup", "report"],
        vertical="speedrun",
        mission_scope="bounded",
    )

    assert blocked is None
    assert allowed is not None and allowed.action == "complete"
