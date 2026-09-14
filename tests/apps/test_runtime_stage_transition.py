from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_helpers import _should_run_stage_transition


@pytest.mark.parametrize(("workflow", "action", "stage"), [("staged", "advance", "optimize"), ("direct", "complete", "setup")])
def test_event_sink_failure_keeps_committed_stage_decision(tmp_path, workflow, action, stage) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.core.pipeline_state import read_pipeline_state
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, "math_synth", workflow_mode=workflow)
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


def test_stale_stage_verdict_preserves_new_campaign_wait(tmp_path) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.manager._core import StageTransition
    from argus_skill.manager.control_state import CampaignControlStore

    control = CampaignControlStore(tmp_path)
    identity = control.campaign_identity(objective="replacement objective", campaign_epoch=2)
    original_head = control.activate_wait(
        identity=identity, wait_id="new-wait", blocker_fingerprint="new-blocker", recheck_token="new-evidence"
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
    assert control.read_snapshot()["active_wait"]["wait_id"] == "new-wait"


@pytest.mark.parametrize("initial_head", [False, True])
def test_unchanged_campaign_accepts_committed_stage_projection(tmp_path, initial_head) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.daemon.state import write_continuous_config
    from argus_skill.manager import Manager
    from argus_skill.manager.control_state import CampaignControlStore
    from argus_skill.skills.vertical_select import persist_vertical

    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, "math_synth", workflow_mode="staged")
    write_continuous_config(state, enabled=True, objective="persisted objective")
    control = CampaignControlStore(state)
    identity = control.campaign_identity()
    if initial_head:
        control.activate_wait(identity=identity, wait_id="old-wait", blocker_fingerprint="old", recheck_token="evidence")
    manager = Manager(project_root=state, execution_workdir=work, runner=object())

    result = StageTransitionMixin._decide_stage_transition(
        SimpleNamespace(manager=manager, _artifact_root=state, _manager_session_root=state),
        rounds_list=[SimpleNamespace(review=ReviewDecision(status="done", reason="verified", next_action=""))],
        workdir=work, sink=SimpleNamespace(handle_event=lambda _event: None),
        mission_scope="bounded", stage_closing=True, continuous_objective="persisted objective",
    )

    head = control.read_head()
    snapshot = control.read_snapshot(head)
    assert result["action"] == "advance"
    assert head.campaign_id == identity.campaign_id
    assert head.state_revision == (2 if initial_head else 1)
    assert result["state_revision"] == head.state_revision
    assert snapshot["active_wait"] is None
    assert snapshot["stage_projection"]["target_stage"] == "optimize"
    assert snapshot["terminal_evidence"][0]["reason"] == "verified"


@pytest.mark.parametrize("change", ["new_wait", "replacement", "generation_only"])
def test_change_after_manager_returns_cannot_be_overwritten_by_projection(tmp_path, change) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.daemon.state import write_continuous_config
    from argus_skill.manager._session_ops import manager_pipeline_lock
    from argus_skill.manager.control_state import CampaignControlStore
    from argus_skill.skills.stage_machine import advance_stage
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "math_synth", workflow_mode="staged")
    write_continuous_config(tmp_path, enabled=True, objective="old objective")
    control = CampaignControlStore(tmp_path)
    identity = control.campaign_identity()
    control.activate_wait(identity=identity, wait_id="old-wait", blocker_fingerprint="old", recheck_token="old")
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
                        control.activate_wait(
                            identity=control.campaign_identity(), wait_id="new-wait",
                            blocker_fingerprint="new", recheck_token="new",
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
    assert snapshot["active_wait"]["wait_id"] == ("old-wait" if change == "generation_only" else "new-wait")
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


def test_stage_closing_planner_node_reaches_manager_stage_writer() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        require_independent_review=True,
        review_source="reviewer",
        preplanned=True,
        stage_closing=True,
    )


def test_direct_reviewed_work_preserves_legacy_stage_transition() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        review_source="reviewer",
        preplanned=False,
        stage_closing=False,
    )


def test_final_submission_is_stage_eligible_without_bounded_flag() -> None:
    assert _should_run_stage_transition(
        "done",
        mission_scope="final_submission",
        review_source="reviewer",
        preplanned=True,
        stage_closing=False,
    )


def test_worker_without_stage_authority_never_reaches_the_stage_writer() -> None:
    """A dispatched teammate runs in the project root and must not write its stage.

    The shape a teammate actually presents: no bounded scope (it is handed one
    task, not a Planner node), a vertical that does not require independent
    review, and a Reviewer verdict — which is exactly the combination the
    legacy tail of the guard lets through. N of them run concurrently against
    one ``.argus/PIPELINE_STATE.json``.
    """
    kwargs = dict(
        mission_scope="",
        require_independent_review=False,
        review_source="reviewer",
        preplanned=False,
        stage_closing=False,
    )
    assert _should_run_stage_transition("done", **kwargs)
    assert not _should_run_stage_transition(
        "done", holds_stage_authority=False, **kwargs
    )


def test_withheld_stage_authority_outranks_every_other_eligibility_route() -> None:
    """Not a "which kind of work is this" question, so nothing overrides it.

    ``final_submission`` and an independent-review entitlement are the two
    strongest reasons a mission may move the stage; neither makes a subordinate
    worker the project's stage authority.
    """
    assert not _should_run_stage_transition(
        "done",
        mission_scope="final_submission",
        require_independent_review=True,
        review_source="reviewer",
        stage_closing=True,
        holds_stage_authority=False,
    )


def test_stage_closing_runtime_path_uses_deterministic_manager_writer(
    tmp_path,
) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.manager import Manager
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "math_synth", workflow_mode="staged")

    class Sink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def handle_event(self, event: dict) -> None:
            self.events.append(event)

    sink = Sink()
    runtime = SimpleNamespace(
        manager=Manager(
            project_root=state_root,
            execution_workdir=workdir,
            runner=object(),
        ),
        _artifact_root=state_root,
        _manager_session_root=state_root,
    )
    decision = StageTransitionMixin._decide_stage_transition(
        runtime,
        rounds_list=[SimpleNamespace(review=ReviewDecision(
            status="done",
            reason="Reviewer verified the current-stage evidence.",
            next_action="",
        ))],
        workdir=workdir,
        sink=sink,
        root_task_id="smoke-stage-transition",
        mission_scope="bounded",
        stage_closing=True,
    )

    state = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert decision["action"] == "advance"
    assert decision["source"] == "manager_deterministic"
    assert state["current_stage"] == "optimize"
    assert any(event.get("type") == "life.manager.stage_decision" for event in sink.events)


def test_bounded_direct_runtime_path_retains_manager_adjudication(tmp_path) -> None:
    from argus_skill.apps._runtime_stage_transition import StageTransitionMixin
    from argus_skill.core.models import ReviewDecision
    from argus_skill.manager._core import StageTransition

    class Manager:
        def bind_execution_workdir(self, _workdir):
            return self

        def decide_stage_transition(self, **kwargs):
            assert kwargs["stage_closing"] is False
            return StageTransition(
                "hold",
                "setup",
                "Manager adjudication required",
                current_stage="setup",
                source="manager_llm",
            )

    class Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    decision = StageTransitionMixin._decide_stage_transition(
        SimpleNamespace(
            manager=Manager(),
            _artifact_root=tmp_path,
            _manager_session_root=tmp_path,
        ),
        rounds_list=[
            SimpleNamespace(
                review=ReviewDecision(
                    status="done",
                    reason="Ordinary bounded direct work is complete.",
                    next_action="",
                )
            )
        ],
        workdir=tmp_path,
        sink=Sink(),
        mission_scope="bounded",
        stage_closing=False,
    )

    assert decision["action"] == "hold"
    assert decision["source"] == "manager_llm"


def test_teammate_entry_withholds_stage_authority_from_its_mission() -> None:
    """The flag is only worth having if the teammate actually passes it.

    Asserted against the real ``execute`` signature rather than a stub, because
    the previous attempt at this fix passed a keyword the runner accepted and
    then ignored — the failure mode is silent, so a hand-written double would
    have reproduced the bug rather than caught it.
    """
    import inspect

    from argus_skill.apps._runtime_execute import SkillLoopExecuteMixin
    from argus_skill.team import teammate_entry

    assert "holds_stage_authority" in inspect.signature(
        SkillLoopExecuteMixin.execute
    ).parameters
    source = inspect.getsource(teammate_entry.run_one_engineer_mission)
    assert "holds_stage_authority=False" in source
