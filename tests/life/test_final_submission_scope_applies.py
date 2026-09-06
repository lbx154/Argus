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
- argus_skill/life/supervisor/_planning_context.py
  — ``_final_submission_scope_applies``, ``_planner_task_tags``
- argus_skill/life/supervisor/_planning_cycle_enqueue.py — canonical scope
- argus_skill/life/supervisor/_core.py
  — ``_maybe_skip_inapplicable_final_submission_item``
- argus_skill/life/supervisor/_planning_cycle_helpers.py
  — ``_research_project_done_issue``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.life.memory import BacklogItem
from argus_skill.life.supervisor._constants import (
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
)
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.planner import parse_planner_text
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical_contract

# The two halves of the contract, and what each vertical declares.
CERTIFIED_AND_TARGETED = "research"
TARGETED_ONLY = ("math", "materials")
NEITHER = "software"


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


def test_the_census_that_motivated_this_fix_still_holds() -> None:
    """Guard the premise: these verticals really do declare targets only."""
    for vertical in TARGETED_ONLY:
        contract = load_vertical_contract(vertical)
        assert contract.research_target_levels, (
            f"{vertical} no longer declares research targets; if that is "
            "intended, this whole regression is moot and the test should go"
        )
        assert contract.completion_gate != "certified", (
            f"{vertical} now has a certified completion gate, so the original "
            "predicate would already cover it"
        )


@pytest.mark.parametrize("vertical", TARGETED_ONLY)
def test_a_required_research_target_admits_the_scope(tmp_path, vertical) -> None:
    """The gate demands the scope, so the boundary must persist it."""
    harness = _project(tmp_path, vertical)

    assert harness._final_submission_scope_applies(tmp_path), (
        f"{vertical} requires a certified final_submission mission to clear "
        "_research_project_done_issue, but the scope is treated as inapplicable"
    )


@pytest.mark.parametrize("vertical", TARGETED_ONLY)
def test_the_planner_scope_survives_enqueue(tmp_path, vertical) -> None:
    """Run 10's exact failure: the tag came out ``scope:bounded``."""
    harness = _project(tmp_path, vertical)
    task = SimpleNamespace(scope=PLANNER_SCOPE_FINAL_SUBMISSION)

    tags = harness._planner_task_tags(task)

    assert f"scope:{PLANNER_SCOPE_FINAL_SUBMISSION}" in tags
    assert "bounded_dag_node" not in tags


def test_structural_final_submission_task_produces_consumable_gate_shape(tmp_path) -> None:
    harness = _project(tmp_path, CERTIFIED_AND_TARGETED)
    verdict = parse_planner_text(
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=final certification remains",
                "TASK_KEY=final-certification",
                "TASK_TITLE=Obtain final certification",
                "TASK_OBJECTIVE=Run the final independent Reviewer gate.",
                "TASK_SCOPE=final_submission",
                "TASK_ACCEPTANCE_CHECK=Reviewer PASS is recorded.",
            ]
        )
    )

    assert verdict.error == ""
    tags = harness._planner_task_tags(verdict.new_tasks[0])
    item = BacklogItem.new(
        title=verdict.new_tasks[0].title,
        objective=verdict.new_tasks[0].objective,
        tags=tags,
    )

    assert f"scope:{PLANNER_SCOPE_FINAL_SUBMISSION}" in tags
    assert "bounded_dag_node" not in tags
    assert harness._planner_scope_from_item(item) == PLANNER_SCOPE_FINAL_SUBMISSION


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


def test_a_bounded_vertical_still_downgrades(tmp_path) -> None:
    """The protection this downgrade was written for must stay intact.

    ``software`` has neither half of the contract, so a final-submission task
    there is the stale-state accident ``_maybe_skip_inapplicable_...`` exists
    to retire. Widening the predicate must not widen it to here.
    """
    harness = _project(tmp_path, NEITHER)

    assert not harness._final_submission_scope_applies(tmp_path)
    tags = harness._planner_task_tags(
        SimpleNamespace(scope=PLANNER_SCOPE_FINAL_SUBMISSION)
    )
    assert f"scope:{PLANNER_SCOPE_BOUNDED}" in tags
    assert "bounded_dag_node" in tags


def test_an_undecided_vertical_matches_the_certification_gate(tmp_path) -> None:
    """The new arm must not diverge from the old one on the fallback path.

    ``resolve_vertical`` does not raise for an unresolved project — it warns
    and falls back to ``research``, which carries both halves of the contract.
    So both predicates read ``True`` here. That is pre-existing behavior; what
    this pins is that widening the predicate did not change it.
    """
    harness = _Harness(tmp_path)

    assert harness._final_submission_scope_applies(
        tmp_path
    ) == harness._effective_final_certification_gate(tmp_path)


def test_the_research_arm_never_fires_on_an_inferred_vertical(tmp_path) -> None:
    """The second arm reads the Manager decision only, never the fallback.

    A stale default ``research`` state inferring its way into a paper-final
    task is the exact accident this downgrade exists to prevent, so the new
    arm must not open a second inferred route in. With the certification gate
    off and no Manager decision persisted, the scope stays bounded — even
    though ``resolve_vertical`` would happily answer ``research`` here.
    """
    harness = _Harness(tmp_path, gate=False)

    assert not harness._final_submission_scope_applies(tmp_path)


def test_the_operator_can_still_switch_the_certification_gate_off(
    tmp_path,
) -> None:
    """``final_certification_gate=False`` must not resurrect via the new arm.

    The research target is a *separate* reason to admit the scope, so a
    research project with the config gate disabled still admits it — that is
    the point. What must not happen is the bounded vertical acquiring one.
    """
    persist_vertical(tmp_path, NEITHER)

    assert not _Harness(tmp_path, gate=False)._final_submission_scope_applies(
        tmp_path
    )
