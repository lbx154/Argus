from __future__ import annotations

import json

import pytest

from argus.core.models import ReviewDecision
from argus.manager.stage_decider import final_stage_completion_decision, stage_check
from argus.roles.prompts.manager import build_stage_decision_prompt
from tests import hand_off

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

    assert "to=done when the independently reviewed direct objective" in prompt
    assert "Do not advance merely because later stages exist" in prompt
    assert "to=done only at the final stage" not in prompt


def _check(to: str = "team", current: str = "research", allow_done: bool = False, **fields):
    payload = {"to": to, "summary": "The reviewed evidence decides it.", **fields}
    return stage_check(payload, current_stage=current, stage_order=ORDER, allow_done=allow_done)


def test_advance_goes_to_the_next_stage_or_any_later_one() -> None:
    assert _check(move="advance").target_stage == "plan"
    assert _check(move="advance", target_stage="run").target_stage == "run"
    for target in ("research", "unknown"):
        with pytest.raises(ValueError, match="target_stage must be one of: plan"):
            _check(move="advance", target_stage=target)


def test_rollback_goes_only_to_an_earlier_stage() -> None:
    assert _check(current="run", move="rollback", target_stage="plan").action == "rollback"
    with pytest.raises(ValueError, match="no stage to move to"):
        _check(move="rollback", target_stage="plan")


def test_done_only_at_the_final_stage_or_in_direct_mode() -> None:
    with pytest.raises(ValueError, match="only at the final stage `submission`"):
        _check("done")
    assert _check("done", allow_done=True).action == "complete"
    assert _check("done", current="submission").action == "complete"


def test_the_team_route_names_its_move() -> None:
    with pytest.raises(ValueError, match="to=team needs move"):
        _check()
    assert _check(move="hold").target_stage == "research"




def test_research_advances_from_idea_to_experiment(tmp_path) -> None:
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    (workdir / "RESEARCH_NOTES.md").write_text(
        "# Research notes — Idea stage\n\nSelected idea.",
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
        run_exec=hand_off.stage(move="advance", target_stage="experiment"),
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
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

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
        run_exec=hand_off.stage("done"),
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
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

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
        run_exec=hand_off.stage("done"),
    )

    assert decision.action == "complete"
    assert not (workdir / "paper").exists()


def test_bounded_stage_mission_cannot_complete_staged_research_project(
    tmp_path,
) -> None:
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

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
        run_exec=hand_off.stage("done"),
    )

    assert decision.action == "hold"
    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert state["current_stage"] == "idea"
    assert state.get("stages", {}).get("idea", {}).get("status") != "done"


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

    from argus.manager import stage_decider
    from argus.manager._stage_ops import _StageDecisionMixin

    assert not hasattr(stage_decider, "enforce_scientific_stage_guard")

    params = set(inspect.signature(_StageDecisionMixin._apply_stage_decision_to_disk).parameters)
    assert params == {"self", "decision", "cur", "root", "source"}



def test_reviewer_certified_intermediate_stage_still_uses_manager_judgment(
    optimize_vertical,
    tmp_path,
) -> None:
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, optimize_vertical, workflow_mode="staged")

    decide = hand_off.stage(move="advance", target_stage="measure")

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
    assert decide.prompts and "Advance targets (later stages)" in decide.prompts[0]


@pytest.mark.parametrize(
    ("manager_action", "expected_status"),
    [("hold", "in_progress"), ("complete", "done")],
)
def test_final_stage_completion_requires_manager_decision(
    tmp_path,
    manager_action: str,
    expected_status: str,
) -> None:
    from argus.manager import Manager
    from argus.skills.stage_machine import completion_contract_fingerprint
    from argus.skills.vertical_select import persist_vertical
    from argus.verticals._base import load_vertical_contract

    persist_vertical(tmp_path, "software", workflow_mode="staged")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "delivery"
    state["stages"] = {"delivery": {"status": "in_progress"}}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    version = load_vertical_contract("software", project_root=tmp_path).completion_contract_version
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
        run_exec=hand_off.stage("done") if manager_action == "complete" else hand_off.stage(move="hold"),
    )

    assert decision.action == manager_action
    assert decision.source == "manager_llm"
    assert json.loads(state_path.read_text())["stages"]["delivery"]["status"] == (
        expected_status
    )

