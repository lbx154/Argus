from __future__ import annotations

import json

import pytest

from argus.core.manuscript_snapshot import manuscript_snapshot
from argus.core.models import ReviewDecision
from argus.manager import Manager
from argus.skills.vertical_select import persist_vertical

pytestmark = pytest.mark.usefixtures("optimize_vertical")


def _review(**changes) -> ReviewDecision:
    values = {
        "status": "done",
        "reason": "Reviewer verified every current-stage checklist item.",
        "next_action": "",
    }
    values.update(changes)
    return ReviewDecision(**values)


def _manager(tmp_path, *, workflow_mode: str = "staged"):
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir(parents=True)
    persist_vertical(state_root, "optimize_lab", workflow_mode=workflow_mode)
    return (
        Manager(
            project_root=state_root,
            execution_workdir=workdir,
            runner=object(),
        ),
        state_root,
        workdir,
    )


def _state(root):
    return json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())


def test_stage_closing_stale_manuscript_review_holds(tmp_path) -> None:
    manager, state_root, workdir = _manager(tmp_path)
    manuscript = workdir / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("reviewed\n", encoding="utf-8")
    binding = manuscript_snapshot(workdir, recorded_at="review-time")
    manuscript.write_text("changed\n", encoding="utf-8")

    decision = manager.decide_stage_transition(
        review=_review(manuscript_snapshot=binding),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("stale review must not purchase Manager adjudication")
        ),
    )

    assert decision.action == "hold"
    assert decision.source == "stale_manuscript_review_hold"
    assert decision.reason.startswith("stale: reviewed an earlier manuscript version ")
    assert _state(state_root)["current_stage"] == "setup"


def test_engineer_operator_abort_before_review_holds_without_manager_model(
    tmp_path,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    review = _review(
        status="blocked",
        next_action="This item was intentionally aborted.",
        backend_stop_kind="operator_abort",
        engineer_aborted_before_review=True,
    )

    decision = manager.decide_stage_transition(
        review=review,
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("operator abort before review must not call Manager model")
        ),
    )

    assert decision.action == "hold"
    assert decision.target_stage == "setup"
    assert decision.source == "operator_abort_hold"
    assert decision.diagnostic == "engineer_aborted_before_review"
    assert _state(state_root)["current_stage"] == "setup"


def _no_model(_prompt, _actions=None):
    raise AssertionError("the completion gate asks no model")


def test_direct_final_self_review_closes_through_the_gate(
    tmp_path,
) -> None:
    """The Engineer's acceptance where no review is required is the evidence; no Manager turn runs."""
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "software", workflow_mode="direct")
    manager = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    )

    decision = manager.decide_stage_transition(
        review=_review(review_source="engineer_self_review"),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=_no_model,
    )

    assert decision.action == "complete"
    assert decision.source == "completion_gate"


def test_failed_vertical_completion_preflight_holds_without_a_model(
    tmp_path,
    monkeypatch,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("completion evidence conflicts")

    monkeypatch.setattr(
        "argus.skills.stage_machine._ensure_stage_completion",
        fail_completion,
    )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=_no_model,
    )

    assert decision.action == "hold"
    assert decision.diagnostic == "stage_completion_check_error"
    assert _state(state_root)["current_stage"] == "setup"


def test_open_external_completion_gate_caps_the_stage_without_a_model(
    tmp_path,
    monkeypatch,
) -> None:
    """Below its rework stage an open external gate lets the stage move; it never moves past it."""
    manager, state_root, _workdir = _manager(tmp_path)
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_GATE",
        "MLE_MEDAL_GATE.json:satisfied",
    )
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_REWORK_STAGE",
        "optimize",
    )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=_no_model,
    )

    assert decision.action == "advance"
    order = list(manager.plan_stages("optimize_lab"))
    assert order.index(_state(state_root)["current_stage"]) <= order.index("optimize")


def test_a_task_that_does_not_close_the_stage_moves_nothing(tmp_path) -> None:
    """The Planner proposes a stage ready by planning its closing task; other accepted work holds."""
    manager, state_root, _workdir = _manager(tmp_path)

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state_root, mission_scope="bounded", run_exec=_no_model,
    )

    assert (decision.action, decision.diagnostic) == ("hold", "gate_not_proposed")
    assert _state(state_root)["current_stage"] == "setup"


def test_unaccepted_work_moves_no_stage(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path)

    decision = manager.decide_stage_transition(
        review=_review(status="engineer", reason="Fix the failing check."),
        project_root=state_root, mission_scope="bounded", stage_closing=True, run_exec=_no_model,
    )

    assert (decision.action, decision.diagnostic) == ("hold", "gate_not_accepted")


def test_only_the_gate_declares_done_and_it_honours_the_persons_required_steps(tmp_path) -> None:
    """Accepted work that closes the stage is not "done" while a 必经步骤 is open."""
    from argus.webapi import canvas as store

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "software", workflow_mode="direct")
    store.add_card(state_root, store.read_canvas(state_root)["revision"], kind="step", text="先跑一遍基线")
    manager = Manager(project_root=state_root, execution_workdir=workdir, runner=object())

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state_root, mission_scope="bounded", stage_closing=True,
        run_exec=_no_model,
    )

    assert (decision.action, decision.diagnostic) == ("hold", "gate_completion_rejected")
    assert "required steps are not all done" in decision.reason


def test_a_gate_move_is_told_to_the_person_in_one_short_paragraph(tmp_path) -> None:
    from argus.core.transcript import read_turns

    manager, state_root, _workdir = _manager(tmp_path)

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state_root, mission_scope="bounded", stage_closing=True,
        run_exec=_no_model,
    )

    assert decision.action == "advance"
    [told] = [turn for turn in read_turns(state_root) if str(turn.get("message_id", "")).startswith("stage-advance-")]
    assert told["text"].count("\n") == 0 and "stage is complete" in told["text"]
    assert "advance" not in told["text"] and "gate" not in told["text"]
