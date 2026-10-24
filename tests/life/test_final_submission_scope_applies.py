"""Regression test: a vertical must be able to satisfy its own completion gate.

``scope:final_submission`` is consumed by two independent gates, each keyed on
a different half of the vertical contract:

* ``_journal_has_final_certification`` reads ``completion_gate == "certified"``
* ``_research_project_done_issue`` reads a non-empty ``research_target_levels``

Both are cleared by exactly one artifact — the journal entry
``_mission_execution_settlement`` writes for a succeeded mission carrying
``item_scope == final_submission``. The enqueue-time downgrade was keyed on the
first gate alone, so any vertical declaring research targets *without* a
certified completion gate demanded a scope the enqueue boundary refused to
persist. A census of the shipped verticals found two in exactly that state:
``math`` and ``materials``. Neither could ever reach ``project_done``.

Observed live across testbed runs 8, 9 and 10 (``s-fed750c2``, ``s-1828745c``,
``s-b6efbc62``). With fixes #45 and #46 in place the Planner did emit
``TASK_SCOPE=final_submission``, and the item was still enqueued as::

    'Final-certify the universal m solution'
      tags = ['planner', 'scope:bounded', 'bounded_dag_node', 'stage:scope']

Citations:
- argus/life/supervisor/_planning_context.py
  — ``_final_submission_scope_applies``, ``_planner_task_tags``
- argus/life/supervisor/_planning_cycle_enqueue.py — canonical scope
- argus/life/supervisor/_core.py
  — ``_maybe_skip_inapplicable_final_submission_item``
- argus/life/supervisor/_planning_cycle_helpers.py
  — ``_research_project_done_issue``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.life.supervisor._constants import (
    PLANNER_SCOPE_FINAL_SUBMISSION,
)
from argus.life.supervisor._planning_context import PlanningContextMixin
from argus.skills.vertical_select import persist_vertical

# The two halves of the contract, and what each vertical declares.
CERTIFIED_AND_TARGETED = "research"


class _Harness(PlanningContextMixin):
    """Only the two hooks the scope predicate reaches for."""

    def __init__(self, workdir, *, gate: bool = True) -> None:
        self._workdir = workdir
        self.config = SimpleNamespace(final_certification_gate=gate)

    def _artifact_root(self):
        return self._workdir


def _project(tmp_path, vertical: str):
    persist_vertical(tmp_path, vertical)
    return _Harness(tmp_path)


def test_a_required_research_target_admits_the_scope(tmp_path, targeted_vertical) -> None:
    """The gate demands the scope, so the boundary must persist it."""
    harness = _project(tmp_path, targeted_vertical)

    assert harness._final_submission_scope_applies(tmp_path), (
        f"{targeted_vertical} requires a certified final_submission mission to clear "
        "_research_project_done_issue, but the scope is treated as inapplicable"
    )


@pytest.mark.parametrize("gate", [False, True])
def test_a_certified_vertical_is_unchanged(tmp_path, gate: bool) -> None:
    persist_vertical(
        tmp_path, CERTIFIED_AND_TARGETED, research_target_level="exploratory"
    )
    harness = _Harness(tmp_path, gate=gate)

    assert harness._final_submission_scope_applies(tmp_path)
    assert f"scope:{PLANNER_SCOPE_FINAL_SUBMISSION}" in harness._planner_task_tags(
        SimpleNamespace(scope=PLANNER_SCOPE_FINAL_SUBMISSION)
    )
