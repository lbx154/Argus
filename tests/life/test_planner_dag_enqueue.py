from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.life.memory import Backlog, BacklogItem
from argus.life.supervisor._constants import PLAN_RETRY
from argus.life.supervisor._helpers import (
    _resolve_task_dep_ids,
)
from argus.life.supervisor._planning_cycle_enqueue import (
    PlanningCycleEnqueueMixin,
)
from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState


def test_resolve_dep_ids_dedupes_preserving_order() -> None:
    resolved, unresolved = _resolve_task_dep_ids(
        ["a", "b", "a"],
        {"a": "id-a", "b": "id-b"},
    )
    assert resolved == ["id-a", "id-b"]
    assert unresolved == []


def test_planner_dedup_index_leaves_completed_work_to_the_planner(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    completed = backlog.add(BacklogItem.new(
        title="Publication scale assessment",
        objective="Assess the completed manuscript.",
    ))
    backlog.mark_done(completed.id)
    assert backlog.active() == []

    class Harness(PlanningCycleEnqueueMixin):
        memory = SimpleNamespace(backlog=backlog)

        @staticmethod
        def _planner_scope_from_item(_item):
            return "bounded"

        @staticmethod
        def _item_is_stage_closing(_item):
            return False

        @staticmethod
        def _item_requires_independent_review(_item):
            return True

        @staticmethod
        def _item_skips_stage_transition(_item):
            return False

    state = _PlanCycleState(None)
    assert Harness()._pc_build_dedupe_index(state) is None

    assert completed.id not in {item.id for item in state.seen_signatures.values()}


def test_commit_drops_unknown_dependency_keys_and_tells_the_planner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A dependency key that names no backlog node is dropped: the task is enqueued without it, an event records the dropped
    keys, and the next planner prompt carries the correction once. Rejecting
    the whole plan used to repeat the identical error for dozens of cycles."""
    from argus.life.supervisor._planning_context import (
        PlanningContextMixin,
    )

    backlog = Backlog(tmp_path / "backlog.jsonl")
    events: list[dict[str, object]] = []

    class Harness(PlanningCycleEnqueueMixin, PlanningContextMixin):
        _planning_cycles = 3
        _suggested_sleep_s = 0.0
        _planner_dropped_dependency_keys: list[tuple[str, list[str]]] = []
        memory = SimpleNamespace(backlog=backlog)

        def _project_workdir(self) -> Path:
            return tmp_path

        def _emit(self, event: dict[str, object]) -> None:
            events.append(event)

        def _emit_status(self, _text: str) -> None:
            return None

        def _enter_idle_backoff(self) -> None:
            raise AssertionError("a dropped dependency key is not a planner error")

    state = _PlanCycleState(None)
    state.existing_items = backlog.all()
    state.manager_intent = {}
    select = BacklogItem.new(
        title="Select one build-ready research direction",
        objective="Pick the route the reviews support.",
        node_key="select-direction",
    )
    task = SimpleNamespace(
        deps=["research-idea-pipeline-v8-g1-settlement-gate"],
        impact_score=0,
        impact_area="",
    )
    state.pending_items = [(task, select)]

    harness = Harness()
    assert harness._pc_commit_pending_items(state) is None
    persisted = next(iter(backlog.all()))
    assert persisted.title == "Select one build-ready research direction"
    assert persisted.deps == []
    dropped = next(
        event
        for event in events
        if event["type"] == EventType.LIFE_PLANNER_DEPENDENCY_DROPPED
    )
    assert dropped["dependency_keys"] == [
        "research-idea-pipeline-v8-g1-settlement-gate"
    ]
    assert not any(event["type"] == EventType.LIFE_PLANNER_ERROR for event in events)

    note = harness._planner_dropped_dependency_runtime_note()
    assert "research-idea-pipeline-v8-g1-settlement-gate" in note
    assert "Select one build-ready research direction" in note
    # The correction is delivered once, then cleared.
    assert harness._planner_dropped_dependency_runtime_note() == ""


def test_rejected_stage_request_is_returned_to_planner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus.life.supervisor import _planning_cycle_enqueue

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
        summary="advance to manuscript drafting",
    )

    assert Harness()._pc_apply_stage_request(state) == PLAN_RETRY
    assert feedback == [{
        "stage": "plan",
        "reason": "ValueError: missing plan evidence",
        "diagnostic": "stage_completion_gate_failed",
    }]
    assert events[-1]["skip_category"] == "invalid_stage_transition_request"


def test_a_stage_request_without_tasks_moves_the_stage_and_replans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Replay of s-5dc7ab4a, 10-27 07:36: the reviewed idea asked to move on.

    The Planner handed off to the Engineer with advance_to_stage=experiment and
    no new tasks. The handoff branch returned before the stage request was read,
    so the stage stayed at idea, no task existed, and the project sat idle for
    13 hours. The request now runs before the handoff, and a stage that moved
    with no tasks for it sends the Planner back to plan the new stage.
    """
    from argus.life.supervisor import _planning_cycle_enqueue

    stage = ["idea"]

    def apply(**kwargs: object) -> None:
        stage[0] = str(kwargs["requested_stage"])

    monkeypatch.setattr(_planning_cycle_enqueue, "_apply_planner_stage_request", apply)

    class Harness(PlanningCycleEnqueueMixin):
        _planning_cycles = 1

        def _project_workdir(self) -> Path:
            return tmp_path

        def _artifact_root(self) -> Path:
            return tmp_path

        def _current_pipeline_stage(self) -> str:
            return stage[0]

        def _reset_idle_backoff(self) -> None:
            pass

    state = _PlanCycleState(None)
    state.verdict = SimpleNamespace(
        new_tasks=[], advance_to_stage="experiment", handoff="engineer",
        summary="复用已通过的独立审查，不再追加选题或重复评议。后续先核实实时磁盘余量、授权设备及宿主预留，再确定紧凑部署方案并测量真实推理与持久记忆更新成本。",
    )

    assert Harness()._pc_apply_stage_request(state) == PLAN_RETRY
    assert stage == ["experiment"]


def test_all_filtered_tasks_persist_feedback_for_next_planner_cycle() -> None:
    feedback: list[dict[str, str]] = []

    class Harness(PlanningCycleEnqueueMixin):
        _planning_cycles = 3

        @staticmethod
        def _current_pipeline_stage() -> str:
            return "analysis"

        @staticmethod
        def _emit_planner_verdict(**_kwargs: object) -> bool:
            return True

        @staticmethod
        def _enter_idle_backoff() -> None:
            return None

        @staticmethod
        def _emit_status(_text: str) -> None:
            return None

        @staticmethod
        def _persist_manager_planner_feedback(**kwargs: str) -> bool:
            feedback.append(kwargs)
            return True

    state = _PlanCycleState(None)
    state.verdict = SimpleNamespace(
        new_tasks=[SimpleNamespace(title="Refresh paper review")],
        project_done=False,
        summary="the existing review is stale",
        retire_tasks=[],
    )
    state.skipped_task_feedback = [{
        "title": "Refresh paper review",
        "category": "vertical_task_policy",
        "reason": "the task names no stage deliverable",
    }]

    assert Harness()._pc_emit_final_verdict(state) == PLAN_RETRY
    assert feedback == [{
        "stage": "analysis",
        "reason": (
            "Supervisor filtered every task in the previous Planner proposal:\n"
            "- [vertical_task_policy] Refresh paper review: "
            "the task names no stage deliverable"
        ),
        "diagnostic": "planner_tasks_filtered",
    }]
