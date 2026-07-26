"""Twenty of twenty-three verticals could never complete. Regression guard.

Observed live on 2026-07-26 in
``/tmp/argus-night/home/projects/ce3bdc5a5291/events.jsonl`` (and earlier in
``/tmp/argus-ux-home-current/projects/s-5d812960/events.jsonl``): the Reviewer
returned ``done`` and certified the delivery stage, the Manager's stage decision
agreed, the mission completed — and the Planner immediately re-issued the
identical task "Complete and certify the current Goal Gate". Twenty-one provider
calls and about $1.085 in the first session, with no progress.

Three rules interlocked, each defensible alone:

1. ``_planner_task_tags`` downgrades a ``final_submission`` scope to ``bounded``
   for any vertical whose completion gate is not ``full_paper``;
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

from argus_skill.manager.stage_decider import final_stage_completion_decision
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_stage_order,
    vertical_completion_gate,
)


class _CertifiedReview:
    status = "done"
    reason = "Reviewer-certified delivery: implementation plus 8 passing tests."
    next_action = ""
    operator_question = ""


def _every_vertical() -> list[str]:
    from argus_skill.skills import vertical_select

    return sorted(vertical_select.VERTICALS)


def _decide(vertical: str, *, scope: str):
    order = vertical_checklist_stage_order(load_vertical(vertical))
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


def test_every_vertical_can_complete_with_the_scope_it_can_actually_carry() -> None:
    """The end-to-end property, over the real registry.

    A vertical whose Goal Gate mission cannot close its own gate is a vertical
    that can never finish a project. Asserting this per-vertical is what turns a
    three-way interlock into a single visible failure.
    """
    unable: list[str] = []
    for vertical in _every_vertical():
        gate = vertical_completion_gate(load_vertical(vertical))
        # The scope the enqueue boundary will actually persist for this
        # vertical: `final_submission` survives only on the paper track.
        carried = "final_submission" if gate == "full_paper" else "bounded"
        decision, order = _decide(vertical, scope=carried)
        if not order:
            continue
        if decision is None or decision.action != "complete":
            unable.append(f"{vertical} (gate={gate}, carries scope={carried})")
    assert not unable, (
        "these verticals can never complete — their Goal Gate mission carries a "
        "scope the Manager refuses to act on:\n  " + "\n  ".join(unable)
    )


def test_a_bounded_mission_still_cannot_end_a_paper_submission() -> None:
    """The rule that was worth keeping.

    On the paper track ``final_submission`` is a real gate: a bounded
    sub-mission must not close a submission because its own Reviewer said
    ``done``.
    """
    decision, order = _decide("research", scope="bounded")

    assert order, "research must have a stage order for this test to mean anything"
    assert decision is None


def test_the_paper_track_still_completes_on_its_own_transport() -> None:
    decision, _order = _decide("research", scope="final_submission")

    assert decision is not None and decision.action == "complete"


@pytest.mark.parametrize("vertical", ["software", "kernelbench", "physics"])
def test_the_verticals_seen_livelocked_now_complete(vertical: str) -> None:
    """``software`` is the one caught live; the others share its gate shape."""
    decision, order = _decide(vertical, scope="bounded")

    assert order
    assert decision is not None and decision.action == "complete"


def test_a_non_final_stage_never_completes() -> None:
    """Widening the scope rule must not let a mid-pipeline mission end anything."""
    order = vertical_checklist_stage_order(load_vertical("research"))
    assert len(order) > 1

    decision = final_stage_completion_decision(
        _CertifiedReview(),
        current_stage=order[0],
        stage_order=list(order),
        vertical="research",
        mission_scope="final_submission",
    )

    assert decision is None


def test_an_unreadable_vertical_keeps_the_strict_rule() -> None:
    """Fail closed: an unreadable declaration demands the strict transport."""
    from argus_skill.manager.stage_decider import _mission_scope_can_complete

    assert _mission_scope_can_complete("final_submission", "no-such-vertical") is True
    assert _mission_scope_can_complete("bounded", "no-such-vertical") is False
