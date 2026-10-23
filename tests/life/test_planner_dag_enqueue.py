from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.life.memory import Backlog, BacklogItem
from argus.life.supervisor._constants import PLAN_RETRY
from argus.life.supervisor._helpers import (
    _resolve_task_dep_ids,
    _unique_normalized_task_key_aliases,
)
from argus.life.supervisor._planning_cycle_enqueue import (
    PlanningCycleEnqueueMixin,
    _apply_planner_stage_request,
)
from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState


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


def test_resolve_dep_ids_accepts_unique_normalized_key_alias() -> None:
    aliases = _unique_normalized_task_key_aliases([
        ("Prepare_Data", "id-prepare"),
    ])

    resolved, unresolved = _resolve_task_dep_ids(
        ["PREPARE-data"],
        {"Prepare_Data": "id-prepare"},
        aliases,
    )

    assert resolved == ["id-prepare"]
    assert unresolved == []


def test_resolve_dep_ids_leaves_ambiguous_normalized_alias_unresolved() -> None:
    aliases = _unique_normalized_task_key_aliases([
        ("prepare-data", "id-one"),
        ("prepare_data", "id-two"),
    ])

    resolved, unresolved = _resolve_task_dep_ids(
        ["PREPARE DATA"],
        {"prepare-data": "id-one", "prepare_data": "id-two"},
        aliases,
    )

    assert resolved == []
    assert unresolved == ["PREPARE DATA"]


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

        def _enter_idle_backoff(self) -> None:
            raise AssertionError("known cross-cycle dependency must not back off")

    state = _PlanCycleState(None)
    # The completed node has already moved to backlog.archive.jsonl. Planner
    # dependency resolution must deliberately consult historical node keys.
    state.existing_items = backlog.history()
    state.manager_intent = {}
    state.pending_items = [(task, review)]

    assert Harness()._pc_commit_pending_items(state) is None
    persisted = next(item for item in backlog.all() if item.id == review.id)
    assert persisted.deps == [implementation.id]


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


def test_planner_dedup_index_maps_active_planner_node_key(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    active = backlog.add(BacklogItem.new(
        title="Assess publication-scale evidence gaps",
        objective="Assess the current evidence.",
        node_key="publication-evidence-gap",
    ))

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
    Harness()._pc_build_dedupe_index(state)

    assert state.active_node_keys == {"publication-evidence-gap": active}


def test_commit_resolves_dependency_from_existing_backlog_item_id(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    implementation = backlog.add(
        BacklogItem.new(
            title="Implement the scheduler",
            objective="Complete the implementation.",
            node_key="eng-implement",
        )
    )
    review = BacklogItem.new(
        title="Review the scheduler",
        objective="Review the completed implementation.",
        node_key="review",
    )
    task = SimpleNamespace(
        deps=[implementation.id],
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

        def _enter_idle_backoff(self) -> None:
            raise AssertionError("existing item ids must resolve directly")

    state = _PlanCycleState(None)
    state.existing_items = backlog.all()
    state.manager_intent = {}
    state.pending_items = [(task, review)]

    assert Harness()._pc_commit_pending_items(state) is None
    persisted = next(item for item in backlog.all() if item.id == review.id)
    assert persisted.deps == [implementation.id]


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


def test_commit_resolves_completed_backlog_item_id_dependency(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    completed = backlog.add(
        BacklogItem.new(
            title="Seal the independent verdict",
            objective="Produce the accepted verdict artifact.",
            node_key="review-first-eight",
        )
    )
    backlog.mark_done(completed.id)
    integration = BacklogItem.new(
        title="Integrate accepted rows",
        objective="Consume the sealed verdict and publish accepted rows.",
        node_key="integrate-first-eight",
    )
    task = SimpleNamespace(deps=[completed.id], impact_score=0, impact_area="")

    class Harness(PlanningCycleEnqueueMixin):
        _planning_cycles = 3
        memory = SimpleNamespace(backlog=backlog)

        def _emit(self, _event: dict[str, object]) -> None:
            return None

        def _emit_status(self, _text: str) -> None:
            return None

        def _enter_idle_backoff(self) -> None:
            raise AssertionError("completed item-id dependency must not back off")

    state = _PlanCycleState(None)
    state.existing_items = backlog.all()
    state.manager_intent = {}
    state.pending_items = [(task, integration)]

    assert Harness()._pc_commit_pending_items(state) is None
    persisted = next(item for item in backlog.all() if item.id == integration.id)
    assert persisted.deps == [completed.id]


def test_planner_task_inherits_manager_routing_without_optional_fields() -> None:
    assert PlanningCycleEnqueueMixin._manager_decision_evidence({}) == {
        "routed": True,
    }


def test_planner_stage_request_rolls_back_an_earlier_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus.skills import stage_machine

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

    assert Harness()._pc_build_pending_items(state) == PLAN_RETRY
    assert feedback == [{
        "stage": "plan",
        "reason": "ValueError: missing plan evidence",
        "diagnostic": "stage_completion_gate_failed",
    }]
    assert events[-1]["skip_category"] == "invalid_stage_transition_request"


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
