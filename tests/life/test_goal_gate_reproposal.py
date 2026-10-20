"""Goal Gate re-proposals: what is still a duplicate."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from argus.life.memory import BacklogItem


class _Filter:
    """The real predicate, on a bare object carrying only what it reads."""

    def __init__(self) -> None:
        from argus.life.supervisor._planning_context import (
            PlanningContextMixin,
        )
        from argus.life.supervisor._planning_cycle_enqueue import (
            PlanningCycleEnqueueMixin,
        )

        self._impl = PlanningCycleEnqueueMixin
        self._item_requires_independent_review = (
            PlanningContextMixin._item_requires_independent_review
        )

    def skipped_as_duplicate(self, *, duplicate: BacklogItem, stage_closing: bool) -> bool:
        """True when the proposal is still filtered out."""
        task = SimpleNamespace(stage_closing=stage_closing)
        escapes = self._impl._gate_reproposal_is_not_a_duplicate(self, task, duplicate)
        return not escapes


def _gate_item(status: str, *, review_required: bool = True) -> BacklogItem:
    tags = ["planner", "scope:bounded", "bounded_dag_node", "stage_closing"]
    if review_required:
        tags.append("review:required")
    else:
        tags.append("review:waived")
    return BacklogItem(
        id="015c2e7b0889",
        ts=time.time(),
        title="Complete and certify the current Goal Gate",
        objective="Goal Gate mission for the active staged project.",
        status=status,
        tags=tags,
    )


@pytest.mark.parametrize("status", ["pending", "running"])
def test_an_in_flight_gate_task_is_still_a_duplicate(status: str) -> None:
    # Two concurrent copies of the same in-flight certification would be real
    # duplicate work.
    assert _Filter().skipped_as_duplicate(duplicate=_gate_item(status), stage_closing=True)


def test_a_self_reviewed_prior_task_never_satisfies_a_certification() -> None:
    # The pre-existing exemption, unchanged: review semantics are part of task
    # identity, so a self-reviewed task cannot close a gate that demands
    # independent review.
    assert not _Filter().skipped_as_duplicate(
        duplicate=_gate_item("running", review_required=False),
        stage_closing=True,
    )


def test_planner_tasks_are_tagged_with_the_current_stage() -> None:
    from argus.life.supervisor._planning_context import PlanningContextMixin

    class Harness(PlanningContextMixin):
        @staticmethod
        def _normalize_planner_scope(_value: str) -> str:
            return "bounded"

        @staticmethod
        def _effective_final_certification_gate(_root) -> bool:
            return False

        @staticmethod
        def _artifact_root():
            return "."

        @staticmethod
        def _current_pipeline_stage() -> str:
            return "baseline"

    tags = Harness()._planner_task_tags(
        SimpleNamespace(
            scope="bounded",
            stage_closing=True,
            require_independent_review=True,
            skip_stage_transition=False,
        )
    )

    assert "stage:baseline" in tags

