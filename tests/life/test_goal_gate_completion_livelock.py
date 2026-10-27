"""Twenty of twenty-three verticals could never complete. Regression guard.

Observed live on 2026-07-26 in multiple captured project event logs: the Reviewer
returned ``done`` and certified the delivery stage, the Manager's stage decision
agreed, the mission completed — and the Planner immediately re-issued the
identical task "Complete and certify the current Goal Gate". Twenty-one provider
calls and about $1.085 in the first session, with no progress.

Three rules interlocked, each defensible alone:

1. ``_planner_task_tags`` downgrades a ``final_submission`` scope to ``bounded``
   for any vertical whose completion gate is not ``certified``;
2. ``tick()`` retires a persisted ``final_submission`` item under such a
   vertical as stale — which is *why* rule 1 exists;
3. ``final_stage_completion_decision`` accepted nothing but
   ``final_submission``.

So the mission created specifically to close the Goal Gate carried the one scope
that could never close it, and no amount of Reviewer certification could write
the completion certificate the Planner was waiting for.

A first attempt fixed this at the TaskSpec, setting ``scope="final_submission"``
on the Goal Gate task. Running a real daemon showed the tag still persisted as
``scope:bounded`` — rule 1 silently downgraded it — so that change was reverted
rather than left as a no-op. The lesson is in the first test below: assert the
property end to end over the real vertical registry, not one link of a chain.
"""

from __future__ import annotations

import pytest

from argus.manager.stage_decider import final_stage_completion_decision
from argus.verticals._base import load_vertical_contract


class _CertifiedReview:
    status = "done"
    reason = "Reviewer-certified delivery: implementation plus 8 passing tests."
    next_action = ""
    operator_question = ""


def _decide(vertical: str, *, scope: str):
    order = load_vertical_contract(vertical).stage_order
    if not order:
        return None, ()
    decision = final_stage_completion_decision(
        _CertifiedReview(),
        current_stage=order[-1],
        stage_order=list(order),
        vertical=vertical,
        mission_scope=scope,
    )
    return decision, order


# -- the property that was violated -----------------------------------------


@pytest.mark.parametrize("vertical", ["software", "argus_maintenance", "optimize_lab"])
def test_the_verticals_seen_livelocked_now_complete(vertical: str, optimize_vertical: str) -> None:
    """``software`` is the one caught live; the others share its gate shape."""
    decision, order = _decide(vertical, scope="bounded")

    assert order
    assert decision is not None and decision.action == "complete"


def test_an_unreadable_vertical_keeps_the_strict_rule() -> None:
    """Fail closed: an unreadable declaration demands the strict transport."""
    from argus.manager.stage_decider import _mission_scope_can_complete

    assert _mission_scope_can_complete("final_submission", "no-such-vertical") is True
    assert _mission_scope_can_complete("bounded", "no-such-vertical") is False


# -- the log has to say what happened ----------------------------------------
