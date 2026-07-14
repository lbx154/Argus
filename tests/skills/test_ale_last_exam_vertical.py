from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills_for_vertical,
)
from argus_skill.skills.stage_checklists import (
    ChecklistLoadState,
    format_full_pipeline_checklist,
    format_stage_checklist,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    require_vertical,
    resolve_vertical,
)
from argus_skill.verticals._base import load_vertical, vertical_completion_gate


def _ale_project(tmp_path: Path) -> Path:
    state = tmp_path / "research" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps({"vertical": "ale_last_exam", "current_stage": "execute"}),
        encoding="utf-8",
    )
    return tmp_path


def test_ale_last_exam_is_registered_and_loadable() -> None:
    assert "ale_last_exam" in VERTICALS
    assert "hidden-reference" in VERTICAL_PURPOSES["ale_last_exam"]
    assert require_vertical("ale_last_exam") == "ale_last_exam"

    mod = load_vertical("ale_last_exam")
    assert mod.STAGE_ORDER == ["execute"]
    assert mod.CHECKLIST_STAGE_ORDER == ("execute",)
    assert tuple(mod.STAGE_CHECKS) == ("execute",)
    assert tuple(mod.REVIEWER_CHECKLISTS) == ("execute",)
    assert vertical_completion_gate(mod) == "none"


def test_ale_last_exam_resolves_and_renders_only_delivery_checks(tmp_path: Path) -> None:
    root = _ale_project(tmp_path)

    assert resolve_vertical(root) == "ale_last_exam"
    stage = format_stage_checklist("execute", role="reviewer", project_root=root)
    assert "contract-complete" in stage
    assert "bundle-present" in stage
    assert "artifacts-parse" in stage
    assert "workflow-finished" in stage
    assert "values-measured" in stage
    assert "final-hard-gate-audit" in stage

    full = format_full_pipeline_checklist(role="reviewer", project_root=root)
    assert "Full pipeline checklist (ale_last_exam)" in full
    assert "### execute" in full
    assert "### submission" not in full
    assert "final submission gate" not in full


def test_ale_last_exam_role_banners_pin_hidden_reference_boundaries() -> None:
    mod = load_vertical("ale_last_exam")

    planner = mod.role_banner("planner")
    engineer = mod.role_banner("engineer")
    reviewer = mod.role_banner("reviewer")
    assert "single execute stage" in planner
    assert "Use both terminal and GUI/CUA" in engineer
    assert "do not trust the engineer's summary" in reviewer
    for banner in (planner, engineer, reviewer):
        assert "HIDDEN reference" in banner
        assert "Never seek" in banner
        assert "NOT a paper pipeline" in banner


def test_ale_last_exam_vertical_skills_are_packaged(tmp_path: Path) -> None:
    skills = dict(iter_vertical_skill_texts("ale_last_exam"))

    assert "engineer/ale-last-exam-execution.md" in skills
    assert "reviewer/ale-last-exam-delivery-review.md" in skills
    assert "hidden" in skills["engineer/ale-last-exam-execution.md"].lower()
    assert "Return `done` only" in skills["reviewer/ale-last-exam-delivery-review.md"]

    seed_builtin_skills_for_vertical(tmp_path, "ale_last_exam", overwrite=True)
    seeded_engineer = (
        tmp_path / "engineer" / "ale-last-exam-execution.md"
    ).read_text(encoding="utf-8")
    seeded_reviewer = (
        tmp_path / "reviewer" / "ale-last-exam-delivery-review.md"
    ).read_text(encoding="utf-8")
    assert "MOVED — this is a pointer stub" not in seeded_engineer
    assert "MOVED — this is a pointer stub" not in seeded_reviewer
    assert "## Operating method" in seeded_engineer
    assert "## Review protocol" in seeded_reviewer


def test_ale_last_exam_execute_checklist_is_loaded_and_required(tmp_path: Path) -> None:
    """Req 15: non-Math regression — ale_last_exam checklist is loaded + required."""
    root = _ale_project(tmp_path)

    contract = resolve_stage_checklist_contract("execute", project_root=root)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert len(contract.items) > 0
    assert any("contract-complete" in item.id for item in contract.items)
