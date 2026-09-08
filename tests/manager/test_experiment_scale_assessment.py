from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.life.supervisor._planning_cycle_enqueue import (
    _apply_planner_stage_request,
)
from argus_skill.manager import Manager
from argus_skill.skills.stage_machine import current_stage
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment
from argus_skill.verticals.research.stages import STAGE_CHECKLISTS


def _experiment_manager(tmp_path, *, workflow_mode="staged"):
    state_root = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state_root, "research", workflow_mode=workflow_mode)
    state_path = state_root / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text())
    state["current_stage"] = "experiment"
    state_path.write_text(json.dumps(state))
    return Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ), state_root, workdir


@pytest.mark.parametrize("stage_closing", [False, True])
def test_passing_experiment_waits_for_planner_even_on_fast_path(
    tmp_path, stage_closing,
):
    manager, root, _workdir = _experiment_manager(tmp_path)
    decision = manager.decide_stage_transition(
        review=ReviewDecision(status="done", reason="The configured run passed.", next_action=""),
        stage_closing=stage_closing,
        continuous_objective="Develop a broadly supported method on existing benchmarks.",
        run_exec=lambda _prompt: pytest.fail("scale assessment must reach Planner first"),
    )

    assert decision.action == "hold"
    assert decision.diagnostic == "experiment_scale_assessment_required"
    assert current_stage(root) == "experiment"


def test_expansion_plan_does_not_advance_but_sufficient_scale_can(tmp_path):
    manager, root, workdir = _experiment_manager(tmp_path)
    review = ReviewDecision(status="done", reason="The configured run passed.", next_action="")
    decision = manager.decide_stage_transition(
        review=review,
        planner_verdict=SimpleNamespace(
            advance_to_stage="",
            reason="Expand to the remaining released task settings.",
        ),
        run_exec=lambda _prompt: pytest.fail("an expansion plan must stay in Experiment"),
    )
    assert decision.action == "hold"
    assert current_stage(root) == "experiment"

    # The normal Planner request is the existing Manager-owned transition route.
    _apply_planner_stage_request(
        state_root=root,
        evidence_root=workdir,
        requested_stage="paper",
        reason="Reviewed expanded evidence covers the objective with adequate precision.",
    )
    assert current_stage(root) == "paper"


def test_manager_receives_planner_adequacy_rationale(tmp_path):
    manager, root, _workdir = _experiment_manager(tmp_path)
    prompts = []

    def decide(prompt):
        prompts.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"advance","target_stage":"paper",'
                '"reason":"Reviewed evidence and Planner scale assessment are sufficient."}'
            )
        )

    decision = manager.decide_stage_transition(
        review=ReviewDecision(status="done", reason="Expanded comparison accepted.", next_action=""),
        planner_verdict=SimpleNamespace(
            advance_to_stage="paper",
            reason="Released task families and repeated runs cover the objective.",
        ),
        run_exec=decide,
    )
    assert decision.action == "advance"
    assert current_stage(root) == "paper"
    assert "Released task families" in prompts[0]


def test_direct_experiment_request_is_not_forced_into_paper(tmp_path):
    manager, root, _workdir = _experiment_manager(tmp_path, workflow_mode="direct")
    decision = manager.decide_stage_transition(
        review=ReviewDecision(status="done", reason="The requested experiment is complete.", next_action=""),
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: pytest.fail("direct completion should stay deterministic"),
    )
    assert decision.action == "complete"
    assert current_stage(root) == "experiment"


def test_standalone_staged_work_without_planner_keeps_existing_transition(tmp_path):
    manager, root, _workdir = _experiment_manager(tmp_path)
    decision = manager.decide_stage_transition(
        review=ReviewDecision(status="done", reason="The experiment is complete.", next_action=""),
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: pytest.fail("standalone transition should stay deterministic"),
    )
    assert decision.action == "advance"
    assert current_stage(root) == "paper"


def test_planner_scale_policy_uses_existing_benchmarks_without_new_mission():
    prompt = render_role_prompt_fragment(
        role="planner",
        operation="continuous",
        stage="experiment",
        scope="",
        project_root=None,
    )
    for required in (
        "After Reviewer accepts",
        "training and evaluation coverage",
        "incremental scale-up",
        "existing benchmarks",
        "small custom benchmarks are allowed",
        "no API calls or only a small amount",
        "reassess total cost when expanding",
        "Respect stricter project-specific restrictions",
        "ADVANCE_TO_STAGE=paper",
        "Do not schedule a separate inspection mission",
    ):
        assert required in prompt
    checklist = " ".join(item.statement for item in STAGE_CHECKLISTS["experiment"])
    assert "Planner must apply" in checklist
    assert "Small custom benchmarks are allowed" in checklist
    assert "cost-aware benchmark policy" in checklist
    paper_prompt = render_role_prompt_fragment(
        role="planner",
        operation="continuous",
        stage="paper",
        scope="",
        project_root=None,
    )
    assert "## Post-result experiment scale assessment" not in paper_prompt


def test_small_custom_benchmarks_are_scientific_evidence_with_cost_accounting():
    playbook = (
        Path(__file__).resolve().parents[2]
        / "argus_skill/verticals/research/skills/research-experiment-playbook.md"
    )
    text = " ".join(playbook.read_text(encoding="utf-8").split())
    for required in (
        "Small custom benchmarks are allowed",
        "scientific evidence for a scoped mechanism claim",
        "generation, labeling, evaluation, judging, and planned repetitions",
        "as well as local compute",
        "does not authorize a large API-backed synthetic campaign",
        "project-specific restrictions",
        "label derivation, splits, controls, and scoring",
        "must not masquerade as official benchmark coverage",
    ):
        assert required in text
    assert "Do not invent a benchmark or synthetic replacement tasks" not in text
