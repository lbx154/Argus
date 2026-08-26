from __future__ import annotations

import json

import pytest

from argus_skill.roles.prompts import (
    ChecklistMode,
    RoleName,
    RolePromptRequest,
    resolve_role_prompt,
)
from argus_skill.roles.prompts.engineer import mission_request
from argus_skill.roles.prompts.manager import (
    FRONT_DOOR,
    stage_decision_request,
)
from argus_skill.roles.prompts.planner import (
    build_continuous_prompt,
    build_continuous_resume_prompt,
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
    path = project_root / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_engineer_banner_resolves_through_role_catalog(tmp_path) -> None:
    persist_vertical(tmp_path, "speedrun")
    vertical = load_vertical("speedrun", project_root=tmp_path)

    engineer = resolve_role_prompt(mission_request(tmp_path))

    assert engineer.vertical == "speedrun"
    assert engineer.role_banner == vertical_role_banner(vertical, "engineer")
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
    assert context.paper_mission is False
    assert context.completion_gate != "certified"
    assert "vertical:speedrun:checklist:planner:stage:optimize" in (
        context.fragment_ids
    )


def test_kernel_parallel_planning_policy_is_vertical_scoped(tmp_path) -> None:
    kernel_root = tmp_path / "kernel"
    software_root = tmp_path / "software"
    persist_vertical(kernel_root, "kernel_engineering")
    persist_vertical(software_root, "software")

    kernel = resolve_role_prompt(continuous_request(kernel_root))
    software = resolve_role_prompt(continuous_request(software_root))

    assert "fill spare mission slots" in kernel.role_banner
    assert "status polling" not in software.role_banner
    assert "vertical:kernel_engineering:banner:planner" in kernel.fragment_ids


def test_reviewer_auto_selects_full_pipeline_for_final_submission(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "research")

    context = resolve_role_prompt(
        evaluate_request(tmp_path, scope="final-submission")
    )

    assert context.scope == "final_submission"
    assert context.paper_mission is True
    assert "## Near-complete paper review" in context.role_banner
    assert "## Final paper review" in context.role_banner
    assert context.stage_checklist == format_full_pipeline_checklist(
        role="reviewer",
        project_root=tmp_path,
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" in (
        context.fragment_ids
    )


def test_research_planner_receives_dynamic_paper_policy(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "run")

    context = resolve_role_prompt(continuous_request(tmp_path))

    assert "## Parallel paper-drafting track" in context.role_banner
    assert "paper/RESULT_PLACEHOLDERS.md" in context.role_banner
    assert "vertical:research:prompt:planner:continuous" in context.fragment_ids


def test_research_planner_prompt_keeps_proportional_stage_checklist(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "research")

    prompt = build_continuous_prompt(
        continuous_objective="Develop a publishable research result.",
        journal_tail="",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    resume_prompt = build_continuous_resume_prompt(
        continuous_objective="Develop a publishable research result.",
        journal_tail="",
        planning_cycle=1,
        project_root=tmp_path,
        state_root=tmp_path,
    )

    assert "research.literature" in prompt
    assert "research.literature" in resume_prompt


def test_direct_planner_prompt_omits_stage_checklist(tmp_path) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="direct")

    prompt = build_continuous_prompt(
        continuous_objective="Run the requested experiment directly.",
        journal_tail="",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
    )

    assert "research.literature" not in prompt
    assert "workflow_mode=direct" in prompt


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
    assert context.paper_mission is False
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
