from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.life.supervisor._helpers import _resolve_task_dep_ids
from argus_skill.life.supervisor._planning_cycle_enqueue import (
    PlanningCycleEnqueueMixin,
    _apply_planner_stage_request,
)
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState


def test_resolve_dep_ids_maps_local_keys() -> None:
    resolved, unresolved = _resolve_task_dep_ids(
        ["a", "b"],
        {"a": "id-a", "b": "id-b"},
    )
    assert resolved == ["id-a", "id-b"]
    assert unresolved == []


def test_resolve_dep_ids_empty_deps_is_flat() -> None:
    assert _resolve_task_dep_ids([], {"a": "id-a"}) == ([], [])


def test_resolve_dep_ids_reports_unknown_keys() -> None:
    resolved, unresolved = _resolve_task_dep_ids(
        ["a", "ghost"],
        {"a": "id-a"},
    )
    assert resolved == ["id-a"]
    assert unresolved == ["ghost"]


def test_resolve_dep_ids_dedupes_preserving_order() -> None:
    resolved, unresolved = _resolve_task_dep_ids(
        ["a", "b", "a"],
        {"a": "id-a", "b": "id-b"},
    )
    assert resolved == ["id-a", "id-b"]
    assert unresolved == []


def test_commit_resolves_dependency_from_prior_planning_cycle(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    implementation = backlog.add(
        BacklogItem.new(
            title="Implement the scheduler",
            objective="Complete the implementation.",
            node_key="eng-implement",
        )
    )
    backlog.mark_done(implementation.id)
    review = BacklogItem.new(
        title="Independent Reviewer acceptance check",
        objective="Review the completed implementation.",
        node_key="review",
    )
    task = SimpleNamespace(
        deps=["eng-implement"],
        impact_score=0,
        impact_area="",
    )

    class Harness(PlanningCycleEnqueueMixin):
        _planning_cycles = 2
        memory = SimpleNamespace(backlog=backlog)

        def _emit(self, _event: dict[str, object]) -> None:
            return None

        def _emit_status(self, _text: str) -> None:
            return None

        def _enter_idle_backoff(self) -> float:
            raise AssertionError("known cross-cycle dependency must not back off")

    state = _PlanCycleState(None)
    state.existing_items = backlog.all()
    state.manager_intent = {}
    state.pending_items = [(task, review)]

    assert Harness()._pc_commit_pending_items(state) is None
    persisted = next(item for item in backlog.all() if item.id == review.id)
    assert persisted.deps == [implementation.id]


def test_planner_task_inherits_manager_routing_without_optional_fields() -> None:
    assert PlanningCycleEnqueueMixin._manager_decision_evidence({}) == {
        "routed": True,
    }


def test_planner_stage_request_rolls_back_an_earlier_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus_skill.skills import stage_machine

    calls: list[dict[str, object]] = []

    monkeypatch.setattr(stage_machine, "current_stage", lambda _root: "submission")

    def reject_advance(*_args, **_kwargs) -> None:
        raise ValueError("advance target 'run' must be later than 'submission'")

    def record_rollback(*args, **kwargs) -> str:
        calls.append({"args": args, "kwargs": kwargs})
        return str(tmp_path / "PIPELINE_STATE.json")

    monkeypatch.setattr(stage_machine, "advance_stage", reject_advance)
    monkeypatch.setattr(stage_machine, "rollback_stage", record_rollback)

    _apply_planner_stage_request(
        state_root=tmp_path,
        requested_stage="run",
        reason="Reviewer found missing claim-bearing run evidence.",
        evidence_root=tmp_path,
    )

    assert calls == [{
        "args": (tmp_path,),
        "kwargs": {
            "target_stage": "run",
            "reason": "Reviewer found missing claim-bearing run evidence.",
            "rolled_back_by": "manager:planner_request",
            "evidence_root": tmp_path,
        },
    }]


def test_rejected_stage_request_is_returned_to_planner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus_skill.life.supervisor import _planning_cycle_enqueue

    feedback: list[dict[str, str]] = []
    events: list[dict[str, object]] = []

    class Harness(PlanningCycleEnqueueMixin):
        _planning_cycles = 4

        def _project_workdir(self) -> Path:
            return tmp_path

        def _artifact_root(self) -> Path:
            return tmp_path

        def _current_pipeline_stage(self) -> str:
            return "plan"

        def _persist_manager_planner_feedback(self, **payload: str) -> bool:
            feedback.append(payload)
            return True

        def _reset_idle_backoff(self) -> None:
            pass

        def _emit(self, event: dict[str, object]) -> None:
            events.append(event)

    def reject_stage_request(**_kwargs: object) -> None:
        raise ValueError("missing plan evidence")

    monkeypatch.setattr(
        _planning_cycle_enqueue,
        "_apply_planner_stage_request",
        reject_stage_request,
    )
    state = _PlanCycleState(None)
    state.verdict = SimpleNamespace(
        new_tasks=[],
        advance_to_stage="draft",
        reason="advance to manuscript drafting",
    )

    assert Harness()._pc_build_pending_items(state) == PLAN_RETRY
    assert feedback == [{
        "stage": "plan",
        "reason": "ValueError: missing plan evidence",
        "diagnostic": "stage_completion_gate_failed",
    }]
    assert events[-1]["skip_category"] == "invalid_stage_transition_request"
