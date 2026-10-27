from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.apps._runtime_helpers import _should_run_stage_transition


@pytest.mark.parametrize(("workflow", "action", "stage"), [("staged", "advance", "optimize"), ("direct", "complete", "setup")])
def test_event_sink_failure_keeps_committed_stage_decision(optimize_vertical, tmp_path, workflow, action, stage) -> None:
    from argus.apps._runtime_stage_transition import StageTransitionMixin
    from argus.core.models import ReviewDecision
    from argus.core.pipeline_state import read_pipeline_state
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical

    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, optimize_vertical, workflow_mode=workflow)
    manager = Manager(project_root=state, execution_workdir=work, runner=object())

    class Sink:
        def handle_event(self, _event):
            raise OSError("event sink is unavailable")

    result = StageTransitionMixin._decide_stage_transition(
        SimpleNamespace(manager=manager, _artifact_root=state, _manager_session_root=state),
        rounds_list=[SimpleNamespace(review=ReviewDecision(status="done", reason="verified", next_action=""))],
        workdir=work, sink=Sink(), mission_scope="bounded", stage_closing=True,
    )

    assert result["action"] == action
    assert result["target_stage"] == stage
    assert read_pipeline_state(state)["current_stage"] == stage


def test_stale_stage_verdict_preserves_new_campaign_head(tmp_path) -> None:
    from argus.apps._runtime_stage_transition import StageTransitionMixin
    from argus.core.models import ReviewDecision
    from argus.manager._core import StageTransition
    from argus.manager.control_state import CampaignControlStore

    control = CampaignControlStore(tmp_path)
    identity = control.campaign_identity(objective="replacement objective", campaign_epoch=2)
    original_head, _ = control.commit_revision(
        identity=identity, updates={"stage_projection": {"marker": "new"}}, reason="new campaign"
    )

    class Manager:
        def bind_execution_workdir(self, _workdir):
            return self

        def decide_stage_transition(self, **_kwargs):
            return StageTransition(
                "hold", "setup", "campaign changed", current_stage="setup",
                source="stale_stage_context_hold", diagnostic="stage_context_changed",
            )

    result = StageTransitionMixin._decide_stage_transition(
        SimpleNamespace(manager=Manager(), _artifact_root=tmp_path, _manager_session_root=tmp_path),
        rounds_list=[SimpleNamespace(review=ReviewDecision(status="done", reason="old objective verified", next_action=""))],
        workdir=tmp_path, sink=SimpleNamespace(handle_event=lambda _event: None),
        continuous_objective="old objective", mission_scope="bounded", stage_closing=True,
    )

    assert result["source"] == "stale_stage_context_hold"
    assert control.read_head() == original_head
    assert control.read_snapshot()["stage_projection"] == {"marker": "new"}


@pytest.mark.parametrize("change", ["new_revision", "replacement", "generation_only"])
def test_change_after_manager_returns_cannot_be_overwritten_by_projection(optimize_vertical, tmp_path, change) -> None:
    from argus.apps._runtime_stage_transition import StageTransitionMixin
    from argus.core.models import ReviewDecision
    from argus.daemon.state import write_continuous_config
    from argus.manager._session_ops import manager_pipeline_lock
    from argus.manager.control_state import CampaignControlStore
    from argus.skills.stage_machine import advance_stage
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, optimize_vertical, workflow_mode="staged")
    write_continuous_config(tmp_path, enabled=True, objective="old objective")
    control = CampaignControlStore(tmp_path)
    identity = control.campaign_identity()
    control.commit_revision(identity=identity, updates={"stage_projection": {"marker": "old"}}, reason="old")
    changed_head = []

    class CommittedDecision:
        action = "advance"
        current_stage = "setup"
        target_stage = "optimize"
        source = "manager_llm"
        diagnostic = "valid_target"

        @property
        def reason(self):
            # The Manager has returned; replace the campaign/control state
            # before the runtime starts publishing that old result.
            if not changed_head:
                with manager_pipeline_lock(tmp_path):
                    if change in {"replacement", "generation_only"}:
                        write_continuous_config(
                            tmp_path, enabled=True,
                            objective="old objective" if change == "generation_only" else "new objective",
                        )
                    if change != "generation_only":
                        control.commit_revision(
                            identity=control.campaign_identity(),
                            updates={"stage_projection": {"marker": "new"}}, reason="new",
                        )
                    changed_head.append(control.read_head())
            return "old objective verified"

    class Manager:
        def bind_execution_workdir(self, _workdir):
            return self

        def decide_stage_transition(self, **_kwargs):
            with manager_pipeline_lock(tmp_path):
                advance_stage(tmp_path, target_stage="optimize", reason="old setup verified")
            return CommittedDecision()

    result = StageTransitionMixin._decide_stage_transition(
        SimpleNamespace(manager=Manager(), _artifact_root=tmp_path, _manager_session_root=tmp_path),
        rounds_list=[SimpleNamespace(review=ReviewDecision(status="done", reason="old objective verified", next_action=""))],
        workdir=tmp_path, sink=SimpleNamespace(handle_event=lambda _event: None),
        mission_scope="bounded", stage_closing=True, continuous_objective="old objective",
    )

    assert result["action"] == "advance"  # The original stage commit still happened.
    assert "state_revision" not in result
    assert control.read_head() == changed_head[0]
    snapshot = control.read_snapshot()
    assert snapshot["stage_projection"] == {"marker": "old" if change == "generation_only" else "new"}
    assert snapshot["terminal_evidence"] == []


def test_non_stage_closing_planner_node_cannot_move_pipeline_stage() -> None:
    assert not _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        require_independent_review=True,
        review_source="reviewer",
        preplanned=True,
        stage_closing=False,
    )


def test_the_task_that_finishes_the_last_required_step_completes_the_project(tmp_path) -> None:
    """The gate is handed the backlog task being judged, not its usage attempt, so its own
    still-running row counts as the step's delivered work."""
    from argus.apps._runtime import _SkillLoopRunner
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.core.models import LoopOutcome, ReviewDecision, RoundRecord
    from argus.life.memory import Backlog, BacklogItem
    from argus.manager import Manager
    from argus.skills.vertical_select import persist_vertical
    from argus.webapi import canvas as store

    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, "software", workflow_mode="direct")
    _canvas, step = store.add_card(state, 0, kind="step", text="ship it")
    item = Backlog(state / "backlog.jsonl").add(BacklogItem.new(title="ship", objective="ship", waypoint_id=step["id"]))
    Backlog(state / "backlog.jsonl").update(item.id, status="running")

    review = ReviewDecision(status="done", reason="verified", next_action="", review_source="engineer_self_review")
    ex_state = _ExecuteState()
    ex_state.workdir = work
    ex_state.mission_scope = "bounded"
    ex_state.outcome = LoopOutcome(
        status="done", reason="", rounds=[RoundRecord(1, "work", 0, review, "", "")],
        final_message="work", workdir=str(work),
    )
    runtime = _SkillLoopRunner.__new__(_SkillLoopRunner)
    runtime.manager = Manager(project_root=state, execution_workdir=work, runner=object())
    runtime._artifact_root = runtime._manager_session_root = state
    runtime._set_usage_context = lambda _value: None
    runtime._extract_execute_outcome_fields(ex_state)
    runtime._maybe_decide_stage_transition(
        ex_state, sink=SimpleNamespace(handle_event=lambda _event: None),
        mission_id=item.id, usage_mission_id=f"{item.id}:attempt:1",
        maintenance_mission=False, skip_stage_transition=False, preplanned=False, stage_closing=True,
    )

    assert ex_state.stage_transition["action"] == "complete", ex_state.stage_transition
