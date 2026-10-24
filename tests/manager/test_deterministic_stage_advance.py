from __future__ import annotations

import json

import pytest

from argus.core.manuscript_snapshot import manuscript_snapshot
from argus.core.models import ReviewDecision
from argus.manager import Manager
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import stage

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


def test_direct_final_self_review_still_uses_manager(
    tmp_path,
) -> None:
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "software", workflow_mode="direct")
    manager = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    )

    manager_model = stage(move="hold")

    decision = manager.decide_stage_transition(
        review=_review(review_source="engineer_self_review"),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(manager_model.prompts) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"


def test_failed_vertical_completion_preflight_keeps_manager_semantics(
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

    manager_model = stage(move="hold")

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(manager_model.prompts) == 1
    assert decision.action == "hold"
    assert _state(state_root)["current_stage"] == "setup"


def test_open_external_completion_gate_keeps_manager_semantics(
    tmp_path,
    monkeypatch,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_GATE",
        "MLE_MEDAL_GATE.json:satisfied",
    )
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_REWORK_STAGE",
        "optimize",
    )

    manager_model = stage(move="hold")

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(manager_model.prompts) == 1
    assert decision.action == "hold"
    assert _state(state_root)["current_stage"] == "setup"
