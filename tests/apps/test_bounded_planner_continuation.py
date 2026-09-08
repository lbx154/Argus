"""Exercise the production bounded Planner boundary without model calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_execute import SkillLoopExecuteMixin
from argus_skill.apps._runtime_helpers import _ExecuteState
from argus_skill.core.operator_context import OperatorContextStore, append_directive
from argus_skill.manager.plan_mode import Plan, PlanStep


class _Harness(SkillLoopExecuteMixin):
    def __init__(self, root: Path) -> None:
        self._args = SimpleNamespace(workdir=str(root), project_state_dir=str(root))
        self._backend = object()
        self._next_seed_thread_id = None


def _state(root: Path) -> _ExecuteState:
    state = _ExecuteState()
    state.workdir = root
    state.config = SimpleNamespace(
        workflow_mode="staged", checkpoint_path=root / "CHECKPOINT.md",
        operator_question_policy_root=root,
    )
    return state


def _capture_plan(monkeypatch) -> list[str]:
    requests: list[str] = []

    def draft(_backend, objective, **_kwargs):
        requests.append(objective)
        return Plan(objective, steps=[PlanStep("Continue from verified B")])

    monkeypatch.setattr("argus_skill.manager.plan_mode.draft_plan", draft)
    return requests


def _plan(harness, state, *, preplanned=False):
    harness._run_bounded_planning(
        state, sink=SimpleNamespace(handle_event=lambda _event: None),
        objective="Complete A then B then C.", original_objective="Original acceptance",
        preplanned=preplanned, mission_id="mission-current",
    )


def test_bounded_planner_receives_latest_continuation_not_engineer_prompt(
    tmp_path: Path, monkeypatch,
) -> None:
    harness, state = _Harness(tmp_path), _state(tmp_path)
    harness._prepare_execute_mission_context(
        state, objective="Complete A then B then C.",
        review_objective="Original acceptance", prelude_context="ENGINEER_ONLY_RUNTIME",
        seed_thread_id=None, scope="bounded",
    )
    state.planner_context = "Shared prior history: A was independently verified."
    state.config.checkpoint_path.write_text("B is complete; do not repeat A or B.")
    requests = _capture_plan(monkeypatch)

    _plan(harness, state)

    assert len(requests) == 1
    assert "B is complete; do not repeat A or B." in requests[0]
    assert "Shared prior history: A was independently verified." in requests[0]
    assert "Complete A then B then C." in requests[0]
    assert "ENGINEER_ONLY_RUNTIME" not in requests[0]
    assert state.review_objective == "Original acceptance"
    assert "ENGINEER_ONLY_RUNTIME" in state.full_task
    assert "Continue from verified B" in state.full_task


def test_bounded_planner_projects_current_role_guidance_without_consuming_once(
    tmp_path: Path, monkeypatch,
) -> None:
    append_directive(tmp_path, "ENGINEER_ONLY_ONCE", applies_to_roles=("engineer",),
                     lifetime="once", expected_revision=0)
    append_directive(tmp_path, "PLAN_C_ONLY", applies_to_roles=("planner",),
                     lifetime="once", expected_revision=1)
    append_directive(tmp_path, "SHARED_NEW_GUIDANCE", lifetime="once", expected_revision=2)
    harness, state = _Harness(tmp_path), _state(tmp_path)
    requests = _capture_plan(monkeypatch)

    _plan(harness, state)

    assert "PLAN_C_ONLY" in requests[0]
    assert "SHARED_NEW_GUIDANCE" in requests[0]
    assert "ENGINEER_ONLY_ONCE" not in requests[0]
    store = OperatorContextStore(tmp_path)
    assert {row.text for row in store.project("engineer", consume_once=False).directives} == {
        "ENGINEER_ONLY_ONCE", "SHARED_NEW_GUIDANCE",
    }
    assert {row.text for row in store.project("planner", consume_once=False).directives} == {
        "PLAN_C_ONLY", "SHARED_NEW_GUIDANCE",
    }
    assert store.acknowledged_revision("planner") == 0


def test_bounded_planner_reads_only_the_current_mission_checkpoint(
    tmp_path: Path, monkeypatch,
) -> None:
    harness, state = _Harness(tmp_path), _state(tmp_path)
    (tmp_path / "CHECKPOINT.md").write_text("UNRELATED_PROJECT_CHECKPOINT")
    checkpoint = tmp_path / "handoffs" / "mission-current" / "CHECKPOINT.md"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("CURRENT_MISSION: B is verified; continue C.")
    state.config.checkpoint_path = checkpoint
    requests = _capture_plan(monkeypatch)

    _plan(harness, state)

    assert "CURRENT_MISSION: B is verified; continue C." in requests[0]
    assert "UNRELATED_PROJECT_CHECKPOINT" not in requests[0]
    assert checkpoint.read_text() == "CURRENT_MISSION: B is verified; continue C."


@pytest.mark.parametrize("preplanned,workflow", [(True, "staged"), (False, "direct")])
def test_existing_planning_skip_does_not_touch_context(
    tmp_path: Path, monkeypatch, preplanned: bool, workflow: str,
) -> None:
    harness, state = _Harness(tmp_path), _state(tmp_path)
    state.config.workflow_mode = workflow
    requests = _capture_plan(monkeypatch)

    _plan(harness, state, preplanned=preplanned)

    assert requests == []
    assert not (tmp_path / "operator_context.json").exists()


class _RuntimeProbe(_Harness):
    """Use execute() and its production planning phase, skipping model execution."""

    def _execute_chat_fast_path(self, **_kwargs):
        return None

    def _build_execute_config(self, state, **_kwargs):
        prepared = _state(Path(self._args.workdir))
        state.workdir, state.config = prepared.workdir, prepared.config

    def _build_execute_skill_store_and_loop(self, state, **_kwargs):
        pass

    def _invoke_execute_loop(self, state, **kwargs):
        kwargs.pop("usage_mission_id")
        self._run_bounded_planning(state, **kwargs)
        self.state = state

    def _extract_execute_outcome_fields(self, state):
        pass

    def _maybe_decide_stage_transition(self, state, **_kwargs):
        pass

    def _build_execute_outcome(self, state):
        return SimpleNamespace(success=True)


def test_supervisor_to_execute_to_planner_preserves_shared_continuation(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus_skill.life.supervisor._mission_execution_helpers import _MissionRunState

    memory = LifeMemory.open(tmp_path / "life")
    sink = SimpleNamespace(handle_event=lambda _event: None)
    runner = _RuntimeProbe(memory.root)
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(runtime_context="ENGINEER_ONLY_RUNTIME"),
    )
    item = BacklogItem.new(
        title="Continue current mission", objective="Complete A then B then C.",
        acceptance_check="C passes the original independent check",
        non_goals=["do not relax the threshold"],
    )
    memory.journal.path.write_text(json.dumps({
        "type": "life.mission.completed", "item_id": "previous-attempt",
        "title": "A verified", "summary": "A passed the original check.",
        "success": True,
    }) + "\n")
    append_directive(memory.root, "ENGINEER_ROLE_ONLY", applies_to_roles=("engineer",),
                     lifetime="once", expected_revision=0)
    state = _MissionRunState(item)
    state.prelude = supervisor._build_mission_prelude(item) + "\nVERTICAL_ENGINEER_ONLY"
    state.cost_sink = sink
    state.vertical_root = memory.root
    # A fresh reply arrives after the Engineer snapshot was prepared.
    append_directive(memory.root, "LATEST_OPERATOR_REPLY: continue C only",
                     expected_revision=1)
    (memory.root / "CHECKPOINT.md").write_text("B is complete; do not repeat A or B.")
    requests = _capture_plan(monkeypatch)

    supervisor._invoke_mission_runner(state)

    assert state.exc_str is None
    assert len(requests) == 1
    assert "A passed the original check." in requests[0]
    assert "B is complete; do not repeat A or B." in requests[0]
    assert "LATEST_OPERATOR_REPLY: continue C only" in requests[0]
    for private in ("ENGINEER_ROLE_ONLY", "ENGINEER_ONLY_RUNTIME", "VERTICAL_ENGINEER_ONLY"):
        assert private not in requests[0]
        assert private in runner.state.full_task
    assert runner.state.review_objective == (
        "Complete A then B then C.\n"
        "Acceptance check: C passes the original independent check\n"
        "Non-goals: do not relax the threshold"
    )
    assert item.objective == "Complete A then B then C."
    assert "ENGINEER_ROLE_ONLY" in {
        row.text for row in OperatorContextStore(memory.root).project(
            "engineer", consume_once=False,
        ).directives
    }
