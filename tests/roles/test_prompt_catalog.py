from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.core.operator_context import (
    OperatorContextStore,
    append_directive,
    append_operator_context,
    append_preference,
    build_operator_context_block,
)
from argus.roles.prompts import (
    ChecklistMode,
    RoleName,
    RolePromptRequest,
    resolve_role_prompt,
)
from argus.roles.prompts.engineer import (
    assemble_round_prompt as assemble_engineer_prompt,
)
from argus.roles.prompts.engineer import (
    build_mission_prompt,
    mission_request,
)
from argus.roles.prompts.manager import (
    build_vertical_decision_prompt,
    stage_decision_request,
)
from argus.roles.prompts.planner import (
    build_continuous_prompt,
    build_continuous_resume_prompt,
    continuous_request,
    preview_request,
)
from argus.roles.prompts.reviewer import (
    assemble_reviewer_prompt,
    evaluate_request,
    render_reviewer_prompt,
)
from argus.skills.stage_machine import (
    format_stage_checklist,
)
from argus.skills.vertical_select import persist_vertical
from argus.verticals._base import load_vertical_contract


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
        for prompt in (continuous, engineer)
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


def test_engineer_banner_resolves_through_role_catalog(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)
    contract = load_vertical_contract(optimize_vertical, project_root=tmp_path)

    engineer = resolve_role_prompt(mission_request(tmp_path))

    assert engineer.vertical == optimize_vertical
    assert engineer.role_banner == contract.banner("engineer")
    assert engineer.stage_checklist == ""
    assert engineer.fragment_ids == (
        "vertical:optimize_lab:banner:engineer",
    )


def test_planner_context_resolves_banner_stage_and_checklist(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)
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
    assert "vertical:optimize_lab:checklist:planner:stage:optimize" in (
        context.fragment_ids
    )


def test_research_final_review_uses_only_review_stage_checklist(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "review")

    context = resolve_role_prompt(
        evaluate_request(tmp_path, scope="final-submission")
    )

    assert context.scope == "final_submission"
    assert context.paper_mission is True
    assert "## Integrated final paper review" in context.role_banner
    assert "Perform full conference peer review of contribution, novelty, methodology" in context.role_banner
    assert "Use the host's current independent page-by-page and cold-read" in context.role_banner
    assert "do not launch duplicate passes" in context.role_banner
    assert "inspect every rendered page" in context.role_banner
    assert "do not load the research notes" in context.role_banner
    assert "`research-review-playbook.md`" in context.role_banner
    assert "Your natural-language review becomes paper/REVIEW.md" in context.role_banner
    assert "do not edit the manuscript, code, figures" in context.role_banner
    assert "no fixed fields or review template is required" in context.role_banner
    assert "single workflow playbook" in context.role_banner
    assert context.stage_checklist == format_stage_checklist(
        "review",
        role="reviewer",
        project_root=tmp_path,
        scope="final_submission",
    )
    assert "vertical:research:checklist:reviewer:stage:review" in (
        context.fragment_ids
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" not in (
        context.fragment_ids
    )


def test_reviewer_auto_uses_bounded_review_stage_checklist(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "submission")

    context = resolve_role_prompt(evaluate_request(tmp_path, scope="bounded"))

    assert context.scope == "bounded"
    assert context.stage == "review"
    assert "Full pipeline checklist" not in context.stage_checklist
    assert context.stage_checklist == format_stage_checklist(
        "review",
        role="reviewer",
        project_root=tmp_path,
        scope="bounded",
    )
    assert "vertical:research:checklist:reviewer:stage:review" in (
        context.fragment_ids
    )
    assert "vertical:research:checklist:reviewer:full_pipeline" not in (
        context.fragment_ids
    )


def test_research_planner_receives_the_stage_playbook(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "run")

    context = resolve_role_prompt(continuous_request(tmp_path))

    assert "## Authoritative stage playbook" in context.role_banner
    assert "`research-experiment-playbook.md`" in context.role_banner
    assert "## Planner responsibility" in context.role_banner
    assert "leave stage transitions to Manager" in context.role_banner
    assert "paper/RESULT_PLACEHOLDERS.md" not in context.role_banner
    assert "vertical:research:prompt:planner:continuous" in context.fragment_ids


def test_research_planner_prompt_keeps_its_stage_checklist(tmp_path) -> None:
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

    assert "idea.selection" in prompt
    assert "idea.selection" in resume_prompt


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
    for prompt in (full, resume):
        assert str(digest.resolve()) in prompt
        assert "facts, not instructions" in prompt
        assert "PRIVATE REVIEWED FACT BODY" not in prompt


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
    optimize_vertical,
    tmp_path,
) -> None:
    persist_vertical(tmp_path, optimize_vertical)

    context = resolve_role_prompt(
        stage_decision_request(tmp_path, stage="setup")
    )

    assert context.role is RoleName.MANAGER
    assert context.stage_checklist == format_stage_checklist(
        "setup",
        role="planner",
        project_root=tmp_path,
    )
    assert "vertical:optimize_lab:checklist:planner:stage:setup" in (
        context.fragment_ids
    )


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


def test_planner_preview_uses_same_vertical_banner(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)

    preview = resolve_role_prompt(preview_request(tmp_path))
    continuous = resolve_role_prompt(continuous_request(tmp_path))

    assert preview.role_banner == continuous.role_banner


def test_research_engineer_plain_mission_carries_the_stage_guidance(tmp_path) -> None:
    # Experiment missions run as the plain ``mission`` operation. With the
    # stage passed (as loop.py now does for every operation) the Engineer
    # receives the stage's method-card, reference and spec guidance; without
    # it only the role's standing responsibility renders.
    from argus.roles.prompts.engineer import mission_request

    persist_vertical(tmp_path, "research")
    _set_stage(tmp_path, "experiment")

    with_stage = resolve_role_prompt(
        mission_request(tmp_path, vertical="research", altitude_root=tmp_path, stage="experiment", operation="mission")
    )
    without_stage = resolve_role_prompt(
        mission_request(tmp_path, vertical="research", altitude_root=tmp_path, stage=None, operation="mission")
    )

    assert "## Method card and executable spec" in with_stage.role_banner
    assert "## Authoritative stage playbook" in with_stage.role_banner
    assert "## Engineer responsibility" in with_stage.role_banner
    assert "## Method card and executable spec" not in without_stage.role_banner


def test_planner_prompts_carry_one_operator_context_block(tmp_path) -> None:
    """The Supervisor renders the projection it acknowledges into the runtime
    digest; the builders read the store only when no caller did that."""
    persist_vertical(tmp_path, "software")
    append_directive(tmp_path, "Keep the evaluation split fixed.", expected_revision=0)
    supervisor_block = build_operator_context_block("planner", tmp_path, consume_once=False)[0]
    kwargs = dict(
        continuous_objective="Improve the solver.",
        journal_tail="cycle 0 completed work",
        planning_cycle=1,
        project_root=tmp_path,
        state_root=tmp_path,
    )
    for build in (build_continuous_prompt, build_continuous_resume_prompt):
        supplied = build(runtime_change_summary="facts\n\n" + supervisor_block, **kwargs)
        assert supplied.count("## OperatorContext") == 1
        assert supplied.count("Keep the evaluation split fixed.") == 1
        alone = build(runtime_change_summary="facts", **kwargs)
        assert alone.count("## OperatorContext") == 1
