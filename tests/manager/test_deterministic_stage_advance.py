from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.core.manuscript_snapshot import manuscript_snapshot
from argus_skill.core.models import ReviewDecision
from argus_skill.manager import Manager
from argus_skill.skills.vertical_select import persist_vertical


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
    persist_vertical(state_root, "speedrun", workflow_mode=workflow_mode)
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


def test_stage_closing_reviewer_done_advances_without_manager_model(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        raise AssertionError("unambiguous Reviewer done must not call Manager model")

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert calls == []
    assert decision.action == "advance"
    assert decision.target_stage == "optimize"
    assert decision.source == "manager_deterministic"
    assert decision.diagnostic == "deterministic_reviewer_done"
    assert _state(state_root)["current_stage"] == "optimize"


def test_stage_closing_bounded_direct_done_with_advice_completes_current_stage(
    tmp_path,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path, workflow_mode="direct")
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        raise AssertionError("direct Reviewer done must not call Manager model")

    decision = manager.decide_stage_transition(
        review=_review(
            planner_report={
                "forward_progress": True,
                "plan_signal": "reconsider",
                "challenge": "A broader causal claim needs implementation evidence.",
                "alternative": "Compare executed configurations before expanding it.",
                "authority_impact": "technical",
            }
        ),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert calls == []
    assert decision.action == "complete"
    assert decision.target_stage == "setup"
    assert decision.source == "manager_deterministic"
    assert decision.diagnostic == "deterministic_reviewer_done"
    state = _state(state_root)
    assert state["current_stage"] == "setup"
    assert state["stages"]["setup"]["status"] == "done"


def test_bounded_direct_done_does_not_require_staged_learning_bundle(
    tmp_path,
) -> None:
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "learning", workflow_mode="direct")
    manager = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: pytest.fail(
            "direct Reviewer done must not require staged learning artifacts"
        ),
    )

    assert decision.action == "complete"
    assert decision.target_stage == "ingest"
    state = _state(state_root)
    assert state["stages"]["ingest"]["status"] == "done"
    assert state["stages"]["study"]["status"] == "skipped"
    assert state["stages"]["curate"]["status"] == "skipped"
    assert state["stages"]["review"]["status"] == "skipped"


def test_stage_closing_open_ended_direct_done_advances(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path, workflow_mode="direct")

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        open_ended=True,
        run_exec=lambda _prompt: pytest.fail(
            "unambiguous Reviewer done must not call Manager model"
        ),
    )

    assert decision.action == "advance"
    assert decision.target_stage == "optimize"
    assert decision.source == "manager_deterministic"
    assert decision.diagnostic == "deterministic_reviewer_done"
    assert _state(state_root)["current_stage"] == "optimize"


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


def test_ordinary_bounded_direct_done_keeps_manager_adjudication(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"ordinary work does not close the stage"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=False,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"
    assert _state(state_root)["current_stage"] == "setup"


@pytest.mark.parametrize("workflow_mode", ["direct", "staged"])
def test_stage_closing_final_done_completes_without_manager_model(
    tmp_path,
    workflow_mode: str,
) -> None:
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "software", workflow_mode=workflow_mode)
    manager = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    )
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        raise AssertionError("clean terminal acceptance must not call Manager model")

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert calls == []
    assert decision.action == "complete"
    assert decision.target_stage == "delivery"
    assert decision.source == "manager_deterministic"
    assert decision.diagnostic == "deterministic_reviewer_done"
    state = _state(state_root)
    assert state["current_stage"] == "delivery"
    assert state["stages"]["delivery"]["status"] == "done"


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


def test_reviewer_operator_abort_still_uses_manager_model(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"the Reviewer was interrupted"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(
            status="blocked",
            next_action="Retry review.",
            backend_stop_kind="operator_abort",
        ),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"


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

    calls: list[str] = []

    def manager_model(_prompt: str):
        calls.append(_prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"delivery",'
                '"reason":"self-review cannot auto-complete the project"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(review_source="engineer_self_review"),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"


def test_clean_acceptance_with_operator_question_uses_manager(tmp_path) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"operator authority is required"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(operator_question="May the trusted scope be expanded?"),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"


@pytest.mark.parametrize(
    "review",
    [
        SimpleNamespace(
            status="done",
            reason="The local work passed review.",
            next_action="",
            operator_question="",
        ),
        _review(planner_report=["not", "a", "mapping"]),
        _review(frontier_report={"change": 42}),
    ],
)
def test_unparseable_or_absent_review_fields_use_manager(tmp_path, review) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"review controls were incomplete"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=review,
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"


@pytest.mark.parametrize(
    ("review_changes", "mission_scope"),
    [
        ({"status": "blocked", "next_action": "Diagnose the blocker."}, "bounded"),
        ({"status": "replan_requested", "next_action": "Choose a new route."}, "bounded"),
        ({"next_action": "Implement the remaining stage work."}, "bounded"),
        ({"planner_report": {"authority_impact": "operator"}}, "bounded"),
        ({"frontier_report": {"change": "unexplained_regression"}}, "bounded"),
        ({"review_source": "engineer_self_review"}, "bounded"),
    ],
)
def test_ambiguous_or_changed_authority_keeps_manager_semantics(
    tmp_path,
    review_changes,
    mission_scope,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"Manager retained semantic adjudication"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(**review_changes),
        project_root=state_root,
        mission_scope=mission_scope,
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert decision.source == "manager_llm"
    assert _state(state_root)["current_stage"] == "setup"


def test_failed_vertical_completion_preflight_keeps_manager_semantics(
    tmp_path,
    monkeypatch,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("completion evidence conflicts")

    monkeypatch.setattr(
        "argus_skill.skills.stage_machine._ensure_stage_completion",
        fail_completion,
    )

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"completion evidence conflicts"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert _state(state_root)["current_stage"] == "setup"


def test_open_external_completion_gate_keeps_manager_semantics(
    tmp_path,
    monkeypatch,
) -> None:
    manager, state_root, _workdir = _manager(tmp_path)
    calls: list[str] = []
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_GATE",
        "MLE_MEDAL_GATE.json:satisfied",
    )
    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_REWORK_STAGE",
        "optimize",
    )

    def manager_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"hold","target_stage":"setup",'
                '"reason":"external completion gate is open"}'
            )
        )

    decision = manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert len(calls) == 1
    assert decision.action == "hold"
    assert _state(state_root)["current_stage"] == "setup"


def test_replan_rollback_and_illegal_target_still_use_manager(tmp_path) -> None:
    manager, state_root, workdir = _manager(tmp_path)
    manager.decide_stage_transition(
        review=_review(),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: pytest.fail("deterministic setup advance called model"),
    )
    calls: list[str] = []

    def rollback_model(prompt: str):
        calls.append(prompt)
        return SimpleNamespace(
            last_agent_message=(
                '{"action":"rollback","target_stage":"setup",'
                '"reason":"upstream evidence is unreliable"}'
            )
        )

    rollback = manager.decide_stage_transition(
        review=_review(
            status="replan_requested",
            next_action="Rollback to setup and repair the evidence.",
        ),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=rollback_model,
    )
    assert len(calls) == 1
    assert rollback.action == "rollback"
    assert _state(state_root)["current_stage"] == "setup"

    invalid = manager.decide_stage_transition(
        review=_review(status="continue", next_action="Continue."),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"advance","target_stage":"not-a-stage",'
                '"reason":"invalid"}'
            )
        ),
    )
    assert invalid.action == "hold"
    assert invalid.diagnostic == "illegal_advance_target"
    assert _state(state_root)["current_stage"] == "setup"
    assert workdir.is_dir()
