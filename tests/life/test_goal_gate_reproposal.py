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
