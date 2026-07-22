from __future__ import annotations

import json

from argus_skill.manager import Manager
from argus_skill.manager._helpers import _OPTIMIZE_VERTICALS
from argus_skill.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills_for_vertical,
)
from argus_skill.skills.stage_machine import (
    ChecklistLoadState,
    format_stage_checklist,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
    resolve_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_items,
    vertical_checklist_stage_order,
    vertical_completion_gate,
    vertical_requires_independent_review,
    vertical_role_banner,
    vertical_workflow_mode,
)

MATERIALS_SKILLS = {
    "manager/materials-research-manager.md",
    "planner/materials-research-planning.md",
    "engineer/materials-research-execution.md",
    "engineer/materials-ecosystem-routing.md",
    "engineer/materials-data-literature-grounding.md",
    "engineer/materials-atomistic-simulation.md",
    "engineer/materials-cad-cae-process-simulation.md",
    "engineer/materials-experiment-loop.md",
    "reviewer/materials-research-review.md",
    "reviewer/materials-simulation-signoff.md",
    "reviewer/materials-validation-review.md",
}


def test_materials_is_registered_and_loadable() -> None:
    assert "materials" in VERTICALS
    assert "materials processing" in VERTICAL_PURPOSES["materials"]
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)
    assert require_vertical("materials") == "materials"

    mod = load_vertical("materials")
    assert mod.__name__ == "argus_skill.verticals.materials.stages"
    assert mod.STAGE_ORDER == (
        "scope",
        "grounding",
        "model",
        "execute",
        "validate",
        "report",
    )
    assert vertical_checklist_stage_order(mod) == mod.STAGE_ORDER
    assert tuple(mod.STAGE_CHECKS) == mod.STAGE_ORDER
    assert tuple(mod.REVIEWER_CHECKLISTS) == mod.STAGE_ORDER


def test_materials_uses_proportional_independently_reviewed_workflow() -> None:
    mod = load_vertical("materials")

    assert vertical_workflow_mode(mod) == "proportional"
    assert vertical_completion_gate(mod) == "none"
    assert vertical_requires_independent_review(mod) is True
    assert Manager._kind_for("materials") == "custom"
    assert "materials" not in _OPTIMIZE_VERTICALS


def test_materials_persists_and_seeds_scope(tmp_path) -> None:
    persist_vertical(tmp_path, "materials")

    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "materials"
    assert state["current_stage"] == "scope"
    assert resolve_vertical(tmp_path) == "materials"


def test_materials_checklists_cover_science_execution_and_integrity(tmp_path) -> None:
    persist_vertical(tmp_path, "materials")
    mod = load_vertical("materials")
    items = vertical_checklist_items(mod)

    assert set(items) == set(mod.STAGE_ORDER)
    assert all(items[stage] for stage in mod.STAGE_ORDER)
    ids = {item.id for stage_items in items.values() for item in stage_items}
    assert {
        "scope.scale-regime",
        "scope.physical-safety",
        "grounding.data-tools-access",
        "model.material-state",
        "model.calibration-validation-split",
        "execute.real-run",
        "execute.capability-boundary",
        "execute.physical-safety-compliance",
        "validate.numerical-convergence",
        "validate.independent-reference",
        "validate.integrity",
        "report.simulation-experiment-boundary",
    } <= ids

    for stage in mod.STAGE_ORDER:
        contract = resolve_stage_checklist_contract(stage, project_root=tmp_path)
        assert contract.state is ChecklistLoadState.LOADED
        assert contract.items
        rendered = format_stage_checklist(
            stage,
            role="reviewer",
            project_root=tmp_path,
        )
        assert f"Stage checklist ({stage})" in rendered


def test_materials_role_banners_cover_all_four_roles() -> None:
    mod = load_vertical("materials")
    manager = vertical_role_banner(mod, "manager")
    planner = vertical_role_banner(mod, "planner")
    engineer = vertical_role_banner(mod, "engineer")
    reviewer = vertical_role_banner(mod, "reviewer")

    assert "four-role system" in manager
    assert "front door and stage authority" in manager
    assert "scale and route" in planner
    assert "AtomisticSkills" in planner
    assert "MISSION TYPE: MATERIALS RESEARCH" in engineer
    assert "native output" in engineer
    assert "four persistent roles" in reviewer
    assert "independent" in reviewer
    assert "Reviewer" in reviewer


def test_materials_skills_are_packaged_and_seeded(tmp_path) -> None:
    skills = dict(iter_vertical_skill_texts("materials"))
    assert set(skills) == MATERIALS_SKILLS
    assert "Materials Project `mp-api`" in skills[
        "engineer/materials-ecosystem-routing.md"
    ]
    assert "Quantum ESPRESSO" in skills[
        "engineer/materials-atomistic-simulation.md"
    ]
    assert "DEFORM's Python API" in skills[
        "engineer/materials-cad-cae-process-simulation.md"
    ]
    assert "A-Lab and Coscientist" in skills[
        "engineer/materials-experiment-loop.md"
    ]
    assert "calibration data reused as independent validation" in skills[
        "reviewer/materials-simulation-signoff.md"
    ]
    execution_skill = skills["engineer/materials-research-execution.md"].replace(
        "\n", " "
    )
    review_skill = skills["reviewer/materials-simulation-signoff.md"].replace(
        "\n", " "
    )
    assert "Manager-owned lifecycle files as" in execution_skill
    assert "stage advance alone must not require a repair" in execution_skill
    assert (
        "false integrity failure caused only by an authorized Manager stage transition"
        in review_skill
    )

    seeded = seed_builtin_skills_for_vertical(
        tmp_path,
        "materials",
        overwrite=True,
    )
    assert MATERIALS_SKILLS <= set(seeded)
    assert all((tmp_path / path).is_file() for path in MATERIALS_SKILLS)
