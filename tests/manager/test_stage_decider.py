from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.manager.stage_decider import (
    build_stage_decision_prompt,
    fallback_empty_stage_decision,
    final_stage_completion_decision,
    parse_stage_decision,
)

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def _review(status: str = "done") -> ReviewDecision:
    return ReviewDecision(
        status=status,
        reason="Reviewer inspected the evidence and made this judgment.",
        next_action="" if status == "done" else "Continue the work.",
    )


def test_prompt_uses_minimal_reviewer_verdict() -> None:
    prompt = build_stage_decision_prompt(
        current_stage="research",
        next_stage="plan",
        earlier_stages=(),
        checklist_md="Read the actual evidence.",
        review=_review(),
    )

    assert "status: done" in prompt
    assert "Reviewer inspected the evidence" in prompt
    for removed in (
        "scientific_decision",
        "planner_report",
        "Harness arbitration",
        "Reviewer per-item checklist",
    ):
        assert removed not in prompt


def test_direct_stage_prompt_completes_instead_of_advancing() -> None:
    prompt = build_stage_decision_prompt(
        current_stage="idea",
        next_stage="build",
        earlier_stages=(),
        checklist_md="Return one independently reviewed idea.",
        review=_review(),
        allow_early_completion=True,
    )

    assert "COMPLETE at the current stage" in prompt
    assert "Do not ADVANCE merely because later stages exist" in prompt
    assert "COMPLETE only at the final stage" not in prompt


def test_completion_report_prompt_contains_all_stage_information() -> None:
    from argus_skill.roles.prompts.manager import (
        build_project_completion_report_prompt,
    )

    prompt = build_project_completion_report_prompt(
        objective="Complete the project.",
        completion_reason="The final stage is done.",
        completion_context={
            "current_stage": "submission",
            "stages": {
                "research": {"status": "done"},
                "submission": {"status": "done"},
            },
            "stage_history": [
                {
                    "from_stage": "research",
                    "to_stage": "plan",
                    "reason": "research accepted",
                }
            ],
            "rollback_history": [
                {
                    "from_stage": "submission",
                    "to_stage": "draft",
                    "reason": "draft needed repair",
                }
            ],
            "stage_reviews": {
                "research": {"review_status": "done"},
                "submission": {"review_status": "done"},
            },
        },
    )

    assert "completion report, not another review" in prompt
    assert '"research": {' in prompt
    assert '"submission": {' in prompt
    assert "research accepted" in prompt
    assert "draft needed repair" in prompt
    assert '"stage_reviews"' in prompt


def test_parse_advance_immediate_ok() -> None:
    decision = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"ok"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "advance"
    assert decision.target_stage == "plan"


def test_parse_advance_can_skip_irrelevant_stages() -> None:
    decision = parse_stage_decision(
        '{"action":"advance","target_stage":"benchmark","reason":"skip"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "advance"
    assert decision.target_stage == "benchmark"
    assert decision.diagnostic == "valid_skip_target"


def test_parse_complete_can_end_finite_objective_at_current_stage() -> None:
    decision = parse_stage_decision(
        '{"action":"complete","target_stage":"research","reason":"done"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "complete"
    assert decision.target_stage == "research"


def test_parse_advance_still_rejects_current_or_earlier_stage() -> None:
    for target in ("research",):
        decision = parse_stage_decision(
            json.dumps({"action": "advance", "target_stage": target}),
            current_stage="research",
            stage_order=ORDER,
        )
        assert decision.action == "hold"
        assert decision.diagnostic == "illegal_advance_target"


def test_research_advances_from_idea_to_experiment(tmp_path) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    (workdir / "HANDOFF.md").write_text(
        "# HANDOFF — IDEA\n\nSelected idea.",
        encoding="utf-8",
    )
    persist_vertical(state_root, "research", workflow_mode="staged")

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"advance","target_stage":"experiment",'
                '"reason":"the selected idea and handoff are complete"}'
            )
        ),
    )

    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert decision.action == "advance"
    assert decision.target_stage == "experiment"
    assert decision.source == "manager_llm"
    assert state["current_stage"] == "experiment"
    assert state["stages"]["idea"]["status"] == "done"


def test_direct_idea_only_research_can_complete_at_idea(tmp_path) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(
        state_root,
        "research",
        workflow_mode="direct",
        research_target_level="exploratory",
        research_direction_mode="broad",
    )
    review = _review()
    review.research_result = {
        "result_class": "new_candidate",
        "correctness_status": "verified",
        "novelty_status": "verified_new",
        "significance_status": "exploratory",
        "statement_fidelity_status": "verified",
        "evidence": ["independent review"],
        "limitations": [],
    }

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=review,
        project_root=state_root,
        mission_scope="bounded",
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"complete","target_stage":"idea",'
                '"reason":"the finite reviewed objective is complete"}'
            )
        ),
    )

    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert decision.action == "complete"
    assert state["current_stage"] == "idea"
    assert state["stages"]["idea"]["status"] == "done"


def test_publishable_idea_only_can_complete_without_paper_artifacts(
    tmp_path,
) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(
        state_root,
        "research",
        workflow_mode="direct",
        research_target_level="publishable",
        research_direction_mode="broad",
    )
    review = _review()
    review.research_result = {
        "result_class": "new_candidate",
        "correctness_status": "verified",
        "novelty_status": "verified_new",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
        "evidence": ["primary-source novelty review"],
        "limitations": [],
    }

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=review,
        project_root=state_root,
        mission_scope="bounded",
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"complete","target_stage":"idea",'
                '"reason":"the requested publication-quality idea is complete"}'
            )
        ),
    )

    assert decision.action == "complete"
    assert not (workdir / "paper").exists()


def test_bounded_stage_mission_cannot_complete_staged_research_project(
    tmp_path,
) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "research", workflow_mode="staged")

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"complete","target_stage":"research",'
                '"reason":"this bounded stage task is done"}'
            )
        ),
    )

    assert decision.action == "hold"
    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert state["current_stage"] == "idea"
    assert state.get("stages", {}).get("idea", {}).get("status") != "done"


def test_parse_rollback_requires_earlier_stage() -> None:
    valid = parse_stage_decision(
        '{"action":"rollback","target_stage":"plan","reason":"repair"}',
        current_stage="run",
        stage_order=ORDER,
    )
    invalid = parse_stage_decision(
        '{"action":"rollback","target_stage":"draft","reason":"bad"}',
        current_stage="run",
        stage_order=ORDER,
    )
    assert valid.action == "rollback"
    assert invalid.action == "hold"


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_target_formatting_is_normalized(target: str) -> None:
    decision = parse_stage_decision(
        json.dumps({"action": "advance", "target_stage": target, "reason": "ok"}),
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "advance"
    assert decision.target_stage == "plan"


def test_invalid_manager_output_holds() -> None:
    decision = parse_stage_decision(
        '{"action":"unknown"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "hold"


def test_empty_manager_output_always_holds() -> None:
    decision = fallback_empty_stage_decision(
        _review(),
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "hold"
    assert decision.diagnostic == "empty_output_no_manager_judgment"


def test_final_submission_done_can_complete_final_stage() -> None:
    decision = final_stage_completion_decision(
        _review(),
        current_stage="submission",
        stage_order=ORDER,
        mission_scope="final_submission",
    )
    assert decision is not None
    assert decision.action == "complete"


def test_bounded_done_does_not_auto_complete_final_stage() -> None:
    decision = final_stage_completion_decision(
        _review(),
        current_stage="submission",
        stage_order=ORDER,
        mission_scope="bounded",
    )
    assert decision is None


def test_no_second_machine_value_guard_overrides_manager() -> None:
    """The Manager's parsed decision is what reaches disk.

    This used to be pinned by calling ``enforce_scientific_stage_guard`` and
    asserting it returned its input — but that function had been reduced to
    ``_ = review, current_stage; return decision``, an identity function whose
    name still promised a guard. It was deleted; two live call sites went with
    it. The property it stood for is now pinned structurally instead: the write
    path does not receive the reviewer verdict at all, so there is nowhere for a
    second machine gate to reappear without that being visible in the signature.
    """
    import inspect

    from argus_skill.manager import stage_decider
    from argus_skill.manager._stage_ops import _StageDecisionMixin

    assert not hasattr(stage_decider, "enforce_scientific_stage_guard")

    params = set(inspect.signature(_StageDecisionMixin._apply_stage_decision_to_disk).parameters)
    assert params == {"self", "decision", "cur", "root", "source"}

    manager = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"review accepted"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert manager.action == "advance"
    assert manager.target_stage == "plan"


def test_reviewer_certified_intermediate_stage_still_uses_manager_judgment(
    tmp_path,
) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "speedrun", workflow_mode="staged")

    prompts: list[str] = []

    def decide(prompt: str):
        prompts.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"advance","target_stage":"measure",'
                '"reason":"scope is certified; implementation stages do not apply"}'
            )
        )

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        open_ended=True,
        run_exec=decide,
    )

    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert decision.action == "advance"
    assert decision.target_stage == "measure"
    assert decision.source == "manager_llm"
    assert state["current_stage"] == "measure"
    assert state["stages"]["setup"]["status"] == "done"
    assert [
        stage
        for stage in ("optimize",)
        if state["stages"][stage]["status"] == "skipped"
    ] == ["optimize"]
    assert prompts and "Legal ADVANCE targets" in prompts[0]


def test_kernel_direct_vertical_has_no_process_completion_hook(
    tmp_path,
) -> None:
    from argus_skill.verticals._base import (
        load_vertical,
        vertical_stage_completion_issues,
    )

    issues = vertical_stage_completion_issues(
        load_vertical("kernel_engineering"),
        stage="optimize",
        project_root=tmp_path,
    )

    assert issues == ()


@pytest.mark.parametrize(
    ("manager_action", "expected_status"),
    [("hold", "in_progress"), ("complete", "done")],
)
def test_final_stage_completion_requires_manager_decision(
    tmp_path,
    manager_action: str,
    expected_status: str,
) -> None:
    from argus_skill.manager import Manager
    from argus_skill.skills.stage_machine import completion_contract_fingerprint
    from argus_skill.skills.vertical_select import persist_vertical
    from argus_skill.verticals._base import (
        load_vertical,
        vertical_completion_contract_version,
    )

    persist_vertical(tmp_path, "software", workflow_mode="staged")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "delivery"
    state["stages"] = {"delivery": {"status": "in_progress"}}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    version = vertical_completion_contract_version(
        load_vertical("software", project_root=tmp_path)
    )
    state["stages"]["delivery"].update({
        "completion_contract_version": version,
        "completion_contract_sha256": completion_contract_fingerprint(
            tmp_path,
            "delivery",
            version=version,
        ),
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")

    decision = Manager(project_root=tmp_path, runner=object()).decide_stage_transition(
        review=_review(),
        project_root=tmp_path,
        mission_scope="bounded",
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=json.dumps({
                "action": manager_action,
                "target_stage": "delivery",
                "reason": "Manager assessed the operator objective",
            })
        ),
    )

    assert decision.action == manager_action
    assert decision.source == "manager_llm"
    assert json.loads(state_path.read_text())["stages"]["delivery"]["status"] == (
        expected_status
    )
