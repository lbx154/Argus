from __future__ import annotations

import json

from argus_skill.skills.vertical_select import VERTICAL_PURPOSES, VERTICALS, persist_vertical
from argus_skill.verticals import builtin_verticals
from argus_skill.verticals._base import load_vertical_contract


def test_math_synth_is_registered_with_metric_contract(tmp_path) -> None:
    assert "math_synth" in VERTICALS
    assert builtin_verticals() == VERTICALS
    assert "pass@4-minus-pass@1" in VERTICAL_PURPOSES["math_synth"]

    persist_vertical(tmp_path, "math_synth")
    contract = load_vertical_contract("math_synth", project_root=tmp_path)

    assert contract.stage_order == ("setup", "optimize", "measure", "report")
    assert contract.completion_gate == "metric"
    assert contract.workflow_mode == "staged"
    assert contract.checklist_items["report"]


def test_math_synth_stage_completion_requires_metric_and_report(tmp_path) -> None:
    from argus_skill.verticals.math_synth import stages

    assert "summary.json" in " ".join(stages.stage_completion_issues("measure", tmp_path))

    summary = tmp_path / "attempts" / "a1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({"score": 0.25}), encoding="utf-8")
    assert stages.stage_completion_issues("measure", tmp_path) == ()

    assert stages.stage_completion_issues("report", tmp_path)
    (tmp_path / "RESULTS.md").write_text("# Results\n", encoding="utf-8")
    assert stages.stage_completion_issues("report", tmp_path) == ()
