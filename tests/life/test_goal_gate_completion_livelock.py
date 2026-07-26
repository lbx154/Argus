"""The Goal Gate mission must be allowed to close the Goal Gate.

Observed on 2026-07-26 in a real operator session
(/tmp/argus-ux-home-current/projects/s-5d812960/events.jsonl): the Reviewer
returned `done` and certified the delivery stage twice, the Manager's stage
decision agreed both times, and the Planner still re-issued the identical task
"Complete and certify the current Goal Gate" on the next cycle. Twenty-one
provider calls and about $1.085 were spent making no progress.

The cause was a disagreement between two modules that each looked correct on
its own. `_planning_cycle_completion` created the Goal Gate task with
`scope="bounded"`; `stage_decider.final_stage_completion_decision` returns
`None` — never `complete` — for any scope other than `final_submission`. So the
mission created specifically to close the gate was the one mission that could
not close it, and the certificate it was waiting for was never written.

Both sides had tests. Neither noticed, because each tested its own half. The
important test here is therefore not "the scope is final_submission" but "the
scope the Planner emits is one the Manager will act on" — that one fails if
either side is changed alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.supervisor._planning_cycle_helpers import staged_goal_gate_scope
from argus_skill.manager.stage_decider import final_stage_completion_decision


class _Review:
    """A Reviewer verdict that certified the stage."""

    status = "done"
    reason = "Reviewer-certified delivery: implementation and 8 passing tests."
    next_action = ""
    operator_question = ""


def _project(tmp_path: Path, *, vertical: str, stage: str) -> Path:
    """Build the fixture through the real persistence API.

    Hand-writing the state file would test a shape the production code does not
    actually use, which is how the two halves drifted apart in the first place.
    """
    from argus_skill.skills.vertical_select import _state_path, persist_vertical

    persist_vertical(tmp_path, vertical)
    path = _state_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


# -- the join: the two halves must agree ------------------------------------


def test_the_scope_the_planner_emits_is_one_the_manager_will_act_on(
    tmp_path: Path,
) -> None:
    """The regression guard for the livelock, asserting the agreement itself.

    Changing either side alone turns this red, which is exactly what did not
    happen when the two drifted apart.
    """
    root = _project(tmp_path, vertical="software", stage="delivery")

    scope = staged_goal_gate_scope(root)
    decision = final_stage_completion_decision(
        _Review(),
        current_stage="delivery",
        stage_order=["delivery"],
        vertical="software",
        mission_scope=scope,
    )

    assert decision is not None, (
        f"the Goal Gate mission is scoped {scope!r}, which the Manager refuses "
        "to complete — the mission cannot do the one thing it exists for"
    )
    assert decision.action == "complete"


def test_the_bounded_scope_that_caused_the_livelock_is_still_refused() -> None:
    """The scope gate itself is correct and must stay.

    A genuinely bounded sub-mission must not be able to close the whole project
    just because its own Reviewer said `done`. The fix was to scope the Goal
    Gate mission correctly, not to weaken this.
    """
    decision = final_stage_completion_decision(
        _Review(),
        current_stage="delivery",
        stage_order=["delivery"],
        vertical="software",
        mission_scope="bounded",
    )

    assert decision is None


# -- and it must not widen anything else ------------------------------------


def test_a_mid_pipeline_goal_gate_mission_stays_bounded(tmp_path: Path) -> None:
    """Its job is to advance a stage, not to end the project."""
    root = _project(tmp_path, vertical="research", stage="research")

    assert staged_goal_gate_scope(root) == "bounded"


def test_an_unreadable_pipeline_stays_bounded(tmp_path: Path) -> None:
    """Fail closed: if we cannot tell where we are, we cannot be at the end."""
    assert staged_goal_gate_scope(tmp_path / "nowhere") == "bounded"


@pytest.mark.parametrize("stage_case", ["Delivery", "  delivery  "])
def test_the_stage_comparison_is_not_defeated_by_formatting(
    tmp_path: Path, stage_case: str
) -> None:
    """The persisted stage is model-written; casing and spacing vary."""
    root = _project(tmp_path, vertical="software", stage=stage_case)

    assert staged_goal_gate_scope(root) == "final_submission"
