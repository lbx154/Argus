from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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


def _manager(tmp_path):
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir(parents=True)
    persist_vertical(state_root, "speedrun", workflow_mode="staged")
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
        review=_review(
            planner_report={
                "forward_progress": True,
                "plan_signal": "continue",
                "authority_impact": "technical",
            },
            frontier_report={
                "change": "artifact_improved",
                "remaining_work": [],
            },
        ),
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


@pytest.mark.parametrize(
    ("review_changes", "mission_scope"),
    [
        ({"status": "blocked", "next_action": "Diagnose the blocker."}, "bounded"),
        ({"status": "replan_requested", "next_action": "Choose a new route."}, "bounded"),
        ({"operator_question": "May the trusted scope be expanded?"}, "bounded"),
        ({"planner_report": {"authority_impact": "operator"}}, "bounded"),
        ({"planner_report": {"plan_signal": "reconsider"}}, "bounded"),
        ({"frontier_report": {"change": "unexplained_regression"}}, "bounded"),
        ({"review_source": "engineer_self_review"}, "bounded"),
        ({}, "expanded_scope"),
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
