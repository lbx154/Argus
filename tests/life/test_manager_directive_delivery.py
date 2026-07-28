from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps._runtime_execute import _engineer_guidance
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._constants import PLAN_ERROR
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.life.supervisor._planning_cycle_intake import (
    PlanningCycleIntakeMixin,
)
from argus_skill.manager.directive import (
    ACTIVE_MANAGER_DIRECTIVE_PREFIX,
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
        assert state.operator_messages == [
            ACTIVE_MANAGER_DIRECTIVE_PREFIX
            + "do not schedule one-row missions"
        ]


def test_active_directive_reaches_each_engineer_round(tmp_path: Path) -> None:
    _set_objective(tmp_path)
    set_active_manager_directive(tmp_path, "batch the unresolved frontier")

    first = _engineer_guidance(tmp_path, tmp_path)
    second = _engineer_guidance(tmp_path, tmp_path)

    expected = (
        ACTIVE_MANAGER_DIRECTIVE_PREFIX
        + "batch the unresolved frontier"
    )
    assert first == [expected]
    assert second == [expected]
