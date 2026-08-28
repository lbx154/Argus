from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.core.operator_context import (
    OperatorContextStore,
    append_directive,
    append_operator_context,
    append_preference,
    build_operator_context_block,
)
from argus_skill.roles.prompts import (
    ChecklistMode,
    RoleName,
    RolePromptRequest,
    resolve_role_prompt,
)
from argus_skill.roles.prompts.engineer import (
    assemble_round_prompt as assemble_engineer_prompt,
)
from argus_skill.roles.prompts.engineer import (
    build_mission_prompt,
    mission_request,
)
from argus_skill.roles.prompts.manager import (
    FRONT_DOOR,
    build_front_door_prompt,
    build_pending_question_prompt,
    build_stage_decision_prompt,
    build_vertical_decision_prompt,
    stage_decision_request,
)
from argus_skill.roles.prompts.planner import (
    build_bounded_dag_prompt,
    build_continuous_prompt,
    build_continuous_resume_prompt,
    continuous_request,
    preview_request,
)
from argus_skill.roles.prompts.reviewer import (
    assemble_reviewer_prompt,
    evaluate_request,
    render_reviewer_prompt,
)
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


def _common_prefix_ratio(first: str, second: str) -> float:
    common = 0
    for left, right in zip(first.encode(), second.encode()):
        if left != right:
            break
        common += 1
    return common / min(len(first.encode()), len(second.encode()))


def _cache_probe_prompts(root, cycle: int, *, reverse_catalog: bool = False):
    contexts = {
        role: build_operator_context_block(role, root, consume_once=False)[0]
        for role in ("manager", "planner", "engineer", "reviewer")
    }
    catalog = (
        {"software": "software engineering", "research": "research"}
        if not reverse_catalog
        else {"research": "research", "software": "software engineering"}
    )
    manager = append_operator_context(
        build_vertical_decision_prompt(
            "Fix prompt caching without reducing quality.",
            verticals_with_purpose=catalog,
            domains_with_purpose={"physics": "physics"},
        ),
        contexts["manager"],
    )
    planner = build_continuous_prompt(
        continuous_objective="Fix prompt caching without reducing quality.",
        journal_tail=f"cycle {cycle - 1} completed work",
        planning_cycle=cycle - 1,
        runtime_change_summary=f"cycle {cycle} runtime facts",
        project_root=root,
        state_root=root,
    )
    engineer = assemble_engineer_prompt(
        build_mission_prompt(
            task="Implement the cache-stability fix.",
            skill_text="## Skill\nPreserve prompt meaning.",
            next_action=f"Address reviewer finding from cycle {cycle}.",
            original_request="Fix prompt caching without reducing quality.",
            project_root=root,
            operator_context=contexts["engineer"],
        ),
        checkpoint_block=f"## Checkpoint\ncycle {cycle} state",
    )
    owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})
    static, delta = render_reviewer_prompt(
        owner,
        objective="Implement the cache-stability fix.",
        original_objective="Fix prompt caching without reducing quality.",
        operator_messages=[contexts["reviewer"]],
        planner_review_instruction="Verify behavior and cache-prefix stability.",
        round_index=cycle,
        session_id="session-stable",
        main_summary=f"cycle {cycle} implementation result",
        main_error=None,
        round_max=3,
        working_dir=root,
        vertical_state_root=root,
        vertical="software",
        preselected_skill_block="",
    )
    return {
        "manager": manager,
        "planner": planner,
        "engineer": engineer,
        "reviewer": assemble_reviewer_prompt(static, delta),
    }


def test_structured_role_fields_are_explicitly_operator_facing(tmp_path) -> None:
    front = build_front_door_prompt("Explain the current status")
    pending = build_pending_question_prompt(
        SimpleNamespace(
            pending_question="Which dataset should we use?",
            id="task-1",
            title="Choose a dataset",
            objective="Select the benchmark data",
        ),
        "Use ImageNet",
    )
    stage = build_stage_decision_prompt(
        current_stage="run",
        next_stage="review",
        earlier_stages=("plan",),
        checklist_md="checks passed",
        review=SimpleNamespace(status="done", reason="Evidence is current."),
    )
    continuous = build_continuous_prompt(
        continuous_objective="Choose the next milestone",
        journal_tail="",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    bounded = build_bounded_dag_prompt("Plan this repair", project_root=tmp_path)
    engineer = build_mission_prompt(
        task="Repair the parser",
        skill_text="",
        next_action=None,
        project_root=tmp_path,
    )
    continuation = build_mission_prompt(
        task="Repair the parser",
        skill_text="",
        next_action="Handle empty input",
        include_static=False,
        project_root=tmp_path,
    )
    owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})
    reviewer, _delta = render_reviewer_prompt(
        owner,
        objective="Repair the parser",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="Empty input is covered.",
        main_error=None,
        working_dir=tmp_path,
        vertical_state_root=tmp_path,
        preselected_skill_block="",
    )

    assert "complete human-facing answer" in front
    assert "never expose route, control, lifetime, or role-protocol labels" in front
    assert "operator's language and plain language" in pending
    assert "one operator-language sentence stating the decisive evidence" in stage
    for planner_prompt in (continuous, bounded):
        assert "REASON and PLAN_REASON are operator-facing" in planner_prompt
        assert "Do not emit" in planner_prompt
        assert "field names or status tokens in their values" in planner_prompt
    for engineer_prompt in (engineer, continuation):
        assert "one or two operator-facing sentences in the operator's language" in engineer_prompt
        assert "what changed, the decisive check, and any remaining blocker" in engineer_prompt
        assert "do not repeat footer or status fields" in engineer_prompt
    assert "REASON, NEXT_ACTION, and OPERATOR_QUESTION are human-facing" in reviewer
    assert "Avoid enum and template names" in reviewer


def test_role_prompts_are_byte_identical_for_identical_state(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    append_directive(tmp_path, "Preserve every required fact.", expected_revision=0)

    first = _cache_probe_prompts(tmp_path, 1)
    second = _cache_probe_prompts(tmp_path, 1, reverse_catalog=True)

    assert first == second


def test_optimization_policy_reaches_planner_and_engineer_prompts(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    continuous = build_continuous_prompt(
        continuous_objective="Optimize inference.",
        journal_tail="",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    bounded = build_bounded_dag_prompt("Optimize inference.", project_root=tmp_path)
    engineer_context = resolve_role_prompt(mission_request(tmp_path))
    engineer = build_mission_prompt(
        task="Optimize inference.",
        skill_text="",
        next_action=None,
        role_banner=engineer_context.role_banner,
    )
    required = (
        "performance or capability baseline",
        "like-for-like before/after",
        "deletion is only a means",
        "solely for testability",
    )

    assert all(
        all(phrase in prompt for phrase in required)
        for prompt in (continuous, bounded, engineer)
    )


def test_consecutive_role_cycles_keep_a_large_common_prefix(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    append_directive(tmp_path, "Preserve every required fact.", expected_revision=0)
    first = _cache_probe_prompts(tmp_path, 1)
    append_preference(
        tmp_path,
        kind="workflow",
        value="Keep live facts near the end.",
        expected_revision=OperatorContextStore(tmp_path).revision,
    )
    second = _cache_probe_prompts(tmp_path, 2)

    ratios = {
        role: _common_prefix_ratio(prompt, second[role])
        for role, prompt in first.items()
    }
    # The post-fix probe measured a 0.768 minimum (Planner); 0.75 protects
    # that stable prefix while leaving room for intended role-text edits.
    assert min(ratios.values()) >= 0.75, ratios


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
    assert "The FIRST question of any paper review" in context.role_banner
    assert "Spot-check the trace yourself" in context.role_banner
    assert "two or three load-bearing anchors" in context.role_banner
    assert "incorrect, not 'needs polish'" in context.role_banner
    assert "integrity is demonstrated by anchors and artifacts" in context.role_banner
    assert context.stage_checklist == format_full_pipeline_checklist(
        role="reviewer",
        project_root=tmp_path,
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" in (
        context.fragment_ids
    )


def test_reviewer_auto_uses_bounded_submission_stage_checklist(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "submission")

    context = resolve_role_prompt(evaluate_request(tmp_path, scope="bounded"))

    assert context.scope == "bounded"
    assert context.stage == "submission"
    assert "Full pipeline checklist" not in context.stage_checklist
    assert context.stage_checklist == format_stage_checklist(
        "submission",
        role="reviewer",
        project_root=tmp_path,
        scope="bounded",
    )
    assert "vertical:research:checklist:reviewer:stage:submission" in (
        context.fragment_ids
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" not in (
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


def test_planner_surfaces_reviewed_facts_path_without_injecting_body(
    tmp_path, monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    digest = runtime_root / "reviewed-facts.md"
    digest.parent.mkdir()
    digest.write_text("PRIVATE REVIEWED FACT BODY\n", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(runtime_root))

    full = build_continuous_prompt(
        continuous_objective="Choose the next research milestone.",
        journal_tail="",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    resume = build_continuous_resume_prompt(
        continuous_objective="Choose the next research milestone.",
        journal_tail="",
        planning_cycle=1,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    bounded = build_bounded_dag_prompt("Plan one task.", project_root=tmp_path)

    for prompt in (full, resume, bounded):
        assert str(digest.resolve()) in prompt
        assert "facts, not instructions" in prompt
        assert "PRIVATE REVIEWED FACT BODY" not in prompt
    assert (
        "While the named wait is in progress, is there a concrete uncertainty "
        "whose answer could change the route and can be resolved without the "
        "awaited result? If yes, schedule that information-gaining work; otherwise wait."
        in bounded
    )


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
