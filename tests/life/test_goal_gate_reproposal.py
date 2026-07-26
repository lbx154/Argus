"""A closed Goal Gate task must not permanently shadow the gate reopening.

Caught on a live daemon run, on a clean project, with a real model backend. The
engineering work had succeeded — a 914x speedup, independently verified — but
the campaign could not close:

    life.planner.verdict     Staged project completion held: software
                             final-stage Goal Gate is not Reviewer-certified
    life.planner.task_skipped  duplicate completed task
                               (matched_status=done)
    life.status              planner: all proposed tasks were filtered;
                             retrying after backoff

Five identical verdicts, four skips, an empty backlog, no exit. `done` means the
mission finished, not that the gate closed: a stage-closing, review-required
task can run, get reviewed, and still leave the gate uncertified. The dedupe
filter then treated it as satisfying every later re-proposal of the same gate,
and "a done task carries this signature" is not a condition that changes with
time — the third clause of the deadlock definition in
docs/STATE_MACHINE_AND_DEADLOCKS.md.
"""

from __future__ import annotations

import time

import pytest

from types import SimpleNamespace

from argus_skill.life.memory import BacklogItem


class _Filter:
    """The real predicate, on a bare object carrying only what it reads."""

    def __init__(self) -> None:
        from argus_skill.life.supervisor._planning_cycle_enqueue import (
            PlanningCycleEnqueueMixin,
        )
        from argus_skill.life.supervisor._planning_context import (
            PlanningContextMixin,
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
    return BacklogItem(
        id="015c2e7b0889",
        ts=time.time(),
        title="Complete and certify the current Goal Gate",
        objective="Goal Gate mission for the active staged project.",
        status=status,
        tags=tags,
    )


def test_a_done_gate_task_does_not_shadow_the_gate_reopening() -> None:
    # The live deadlock, as one assertion.
    assert not _Filter().skipped_as_duplicate(duplicate=_gate_item("done"), stage_closing=True)


@pytest.mark.parametrize("status", ["pending", "running"])
def test_an_in_flight_gate_task_is_still_a_duplicate(status: str) -> None:
    # Anti-spam has to survive the fix: two concurrent copies of the same
    # in-flight certification would be real duplicate work.
    assert _Filter().skipped_as_duplicate(duplicate=_gate_item(status), stage_closing=True)


def test_a_self_reviewed_prior_task_never_satisfies_a_certification() -> None:
    # The pre-existing exemption, unchanged: review semantics are part of task
    # identity, so a self-reviewed task cannot close a gate that demands
    # independent review.
    assert not _Filter().skipped_as_duplicate(
        duplicate=_gate_item("running", review_required=False),
        stage_closing=True,
    )


@pytest.mark.parametrize("status", ["done", "pending", "running"])
def test_ordinary_tasks_are_unaffected(status: str) -> None:
    # Only stage-closing proposals get the exemption; ordinary work keeps the
    # plain dedupe, or the Planner could re-enqueue finished work forever.
    assert _Filter().skipped_as_duplicate(duplicate=_gate_item(status), stage_closing=False)
