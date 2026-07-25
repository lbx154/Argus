from __future__ import annotations

import json

import pytest

import argus_skill.life.router as life_router
import argus_skill.manager.domain_author as domain_author
import argus_skill.manager.live_view as live_view
import argus_skill.manager.plan_mode as plan_mode
import argus_skill.manager.self_maintenance as self_maintenance
import argus_skill.manager.skill_review as skill_review
import argus_skill.manager.stage_decider as stage_decider
from argus_skill.roles.prompts import (
    ChecklistMode,
    RoleName,
    RolePromptRequest,
    resolve_role_prompt,
)
from argus_skill.roles.prompts import manager as manager_prompts
from argus_skill.roles.prompts.engineer import (
    SKILL_CREATE,
    mission_request,
    skill_request,
)
from argus_skill.roles.prompts.manager import (
    FRONT_DOOR,
    stage_decision_request,
)
from argus_skill.roles.prompts.planner import (
    continuous_request,
    preview_request,
)
from argus_skill.roles.prompts.reviewer import evaluate_request
from argus_skill.skills.stage_machine import (
    format_full_pipeline_checklist,
    format_stage_checklist,
)
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_role_banner


def _set_stage(project_root, stage: str) -> None:
    path = project_root / "research" / "PIPELINE_STATE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_engineer_and_scientist_banners_resolve_through_one_catalog(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "speedrun")
    vertical = load_vertical("speedrun", project_root=tmp_path)

    engineer = resolve_role_prompt(mission_request(tmp_path))
    scientist = resolve_role_prompt(
        skill_request(tmp_path, operation=SKILL_CREATE)
    )

    assert engineer.vertical == "speedrun"
    assert engineer.role_banner == vertical_role_banner(vertical, "engineer")
    assert scientist.role_banner == vertical_role_banner(
        vertical, "scientist_create"
    )
    assert engineer.stage_checklist == ""
    assert engineer.fragment_ids == (
        "vertical:speedrun:banner:engineer",
    )


def test_planner_context_resolves_banner_stage_and_checklist(tmp_path) -> None:
    persist_vertical(tmp_path, "speedrun")
    _set_stage(tmp_path, "optimize")

    context = resolve_role_prompt(continuous_request(tmp_path))

    assert context.role is RoleName.PLANNER
    assert context.stage == "optimize"
    assert context.stage_order == ("setup", "optimize", "measure", "report")
    assert context.stage_checklist == format_stage_checklist(
        "optimize",
        role="planner",
        project_root=tmp_path,
    )
    assert context.completion_gate != "full_paper"
    assert "vertical:speedrun:checklist:planner:stage:optimize" in (
        context.fragment_ids
    )


def test_reviewer_auto_selects_full_pipeline_for_final_submission(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "research")

    context = resolve_role_prompt(
        evaluate_request(tmp_path, scope="final-submission")
    )

    assert context.scope == "final_submission"
    assert context.stage_checklist == format_full_pipeline_checklist(
        role="reviewer",
        project_root=tmp_path,
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" in (
        context.fragment_ids
    )


def test_manager_stage_decision_preserves_planner_checklist_framing(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "speedrun")

    context = resolve_role_prompt(
        stage_decision_request(tmp_path, stage="setup")
    )

    assert context.role is RoleName.MANAGER
    assert context.stage_checklist == format_stage_checklist(
        "setup",
        role="planner",
        project_root=tmp_path,
    )
    assert "vertical:speedrun:checklist:planner:stage:setup" in (
        context.fragment_ids
    )


def test_non_vertical_manager_operation_resolves_empty_context() -> None:
    context = resolve_role_prompt(
        RolePromptRequest(
            role=RoleName.MANAGER,
            operation=FRONT_DOOR,
        )
    )

    assert context.vertical == ""
    assert context.role_banner == ""
    assert context.fragment_ids == ()




def test_unknown_role_operation_fails_loudly(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported reviewer prompt operation"):
        resolve_role_prompt(
            RolePromptRequest(
                role=RoleName.REVIEWER,
                operation="typo",
                project_root=tmp_path,
                checklist_mode=ChecklistMode.NONE,
            )
        )


def test_planner_preview_uses_same_vertical_banner(tmp_path) -> None:
    persist_vertical(tmp_path, "speedrun")

    preview = resolve_role_prompt(preview_request(tmp_path))
    continuous = resolve_role_prompt(continuous_request(tmp_path))

    assert preview.role_banner == continuous.role_banner
