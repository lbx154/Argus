from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps._inbox import queue_inbox_message
from argus_skill.apps._runtime_execute import _engineer_guidance
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.round_config import SupervisedConfig
from argus_skill.engineer.round_reviewer import RoundReviewerMixin
from argus_skill.engineer.round_state import RoundLoopState
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._constants import PLAN_ERROR
from argus_skill.life.supervisor._mission_execution_runtime import (
    MissionExecutionRuntimeMixin,
)
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.life.supervisor._planning_cycle_intake import (
    PlanningCycleIntakeMixin,
)
from argus_skill.manager.directive import (
    STEERING_HEADER,
    set_active_manager_directive,
)


def _set_objective(root: Path) -> None:
    (root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "prove the theorem"}),
        encoding="utf-8",
    )


class _PlannerIntake(PlanningCycleIntakeMixin):
    def __init__(self, root: Path) -> None:
        self.memory = LifeMemory.open(root)
        self.events: list[dict] = []

    def _take_operator_guidance_carryover(self) -> list[str]:
        return []

    def _drain_user_inbox(self) -> list[str]:
        return []

    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def _deactivate_planner_waiting_contract(self) -> None:
        return None

    def _clear_manager_planner_feedback(self) -> None:
        return None

    def _reset_idle_backoff(self) -> None:
        return None


def test_active_directive_reaches_each_planning_cycle(tmp_path: Path) -> None:
    _set_objective(tmp_path)
    set_active_manager_directive(tmp_path, "do not schedule one-row missions")
    supervisor = _PlannerIntake(tmp_path)

    for _ in range(2):
        state = _PlanCycleState(
            revision_request={
                "expected_plan_id": "missing-plan",
                "expected_plan_version": 1,
                "item_id": "missing-item",
            }
        )
        assert supervisor._pc_intake_gate(state) == PLAN_ERROR
        assert len(state.operator_messages) == 1
        assert STEERING_HEADER in state.operator_messages[0]
        assert "do not schedule one-row missions" in state.operator_messages[0]


def test_active_directive_reaches_each_engineer_round(tmp_path: Path) -> None:
    _set_objective(tmp_path)
    set_active_manager_directive(tmp_path, "batch the unresolved frontier")

    first = _engineer_guidance(tmp_path, tmp_path)
    second = _engineer_guidance(tmp_path, tmp_path)

    assert len(first) == len(second) == 1
    assert first == second
    assert STEERING_HEADER in first[0]
    assert "batch the unresolved frontier" in first[0]


def test_new_inbox_messages_accumulate_in_standing_engineer_guidance(
    tmp_path: Path,
) -> None:
    _set_objective(tmp_path)
    queue_inbox_message(tmp_path, "keep the invariant", source="test")
    first = _engineer_guidance(tmp_path, tmp_path)
    queue_inbox_message(tmp_path, "also run the public check", source="test")
    second = _engineer_guidance(tmp_path, tmp_path)

    assert "keep the invariant" in first[0]
    assert "also run the public check" in second[0]
    assert "keep the invariant" in second[0]
    assert second[0].index("also run the public check") < second[0].index(
        "keep the invariant"
    )


def test_standing_steering_reaches_every_mission_prelude(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from argus_skill.life.memory import BacklogItem

    _set_objective(tmp_path)
    set_active_manager_directive(tmp_path, "preserve the public contract")

    class Harness(MissionExecutionRuntimeMixin):
        def __init__(self) -> None:
            self.memory = LifeMemory.open(tmp_path)
            self.config = SimpleNamespace(runtime_context="")

        @staticmethod
        def _render_backlog_item_metadata(_item) -> str:
            return ""

    item = BacklogItem.new(title="mission", objective="implement the change")
    first = Harness()._build_mission_prelude(item)
    second = Harness()._build_mission_prelude(item)

    assert STEERING_HEADER in first
    assert "preserve the public contract" in first
    assert first == second


def test_active_directive_reaches_reviewer(tmp_path: Path) -> None:
    set_active_manager_directive(tmp_path, "review the replacement target")

    class CaptureReviewer:
        def __init__(self) -> None:
            self.kwargs = None

        def evaluate(self, **kwargs):
            self.kwargs = kwargs
            return ReviewDecision(status="done", reason="verified", next_action="")

    class Harness(RoundReviewerMixin):
        def __init__(self) -> None:
            from argus_skill.reviewer import ReviewerConfig

            self.reviewer = CaptureReviewer()
            self.reviewer_config = ReviewerConfig(model="test")

    harness = Harness()
    result = harness._call_reviewer_once(
        objective="stale bounded task",
        original_objective="operator objective",
        round_index=1,
        supervised_config=SupervisedConfig(
            engineer_log_path=str(tmp_path / "events.jsonl"),
        ),
        workdir=tmp_path,
        scope="bounded",
        checkpoint_path=None,
        reviewer_skill_block=None,
        escalate_hint="",
        engineer_result=RunnerResult(exit_code=0, agent_messages=["done"]),
        engineer_message="implemented replacement",
        safe_fatal_error=None,
        process_ownership_note="",
        state=RoundLoopState(),
        on_event=None,
    )

    assert result.status == "done"
    delivered = harness.reviewer.kwargs["operator_messages"]
    assert len(delivered) == 1
    assert STEERING_HEADER in delivered[0]
    assert "review the replacement target" in delivered[0]
