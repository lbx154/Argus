"""A disputed fact the Reviewer cannot observe reaches the Manager by name.

A credential-building line can be masked in every role's view. A Reviewer
that cannot see the line repeats its finding and an Engineer that cannot see it
repeats its fix; more rounds cannot change that. The Reviewer may say so
through its native action, and its review event carries the obstacle by name
so the Manager can steer. Nothing here decides the review: the status and the
round budget are unchanged.
"""

from __future__ import annotations

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.event_catalog import validate_event_envelope
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import review_actions
from tests.hand_off import hand_off

_OBSTACLE = (
    "The Authorization line is masked in my view; a rerunnable local fixture "
    "asserting the header against a placeholder value would settle it."
)


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _queue(backend: MemoryBackend, round_index: int, payload: dict) -> None:
    backend.queue(f"engineer-r{round_index}", CannedResponse(message=f"round {round_index} fixed it"))
    backend.queue("reviewer", CannedResponse(action=("hand_off", payload)))


def test_the_obstacle_rides_any_handoff_but_an_acceptance() -> None:
    # A Reviewer stopped by a check impossible here may replan over it; the
    # obstacle has to ride that verdict too, or a replan erases it.
    for to in ("engineer", "planner", "manager"):
        actions = review_actions()
        actions.dispatch(*hand_off(to, "Header unverified.", unverifiable=_OBSTACLE))
        assert actions.handoff.value.verification_obstacle == _OBSTACLE
    actions = review_actions()
    with pytest.raises(ValueError, match="residual_risk"):
        actions.dispatch(*hand_off("done", "Holds.", unverifiable=_OBSTACLE))


def test_reviewer_obstacle_is_carried_on_the_review_event(tmp_path) -> None:
    backend = MemoryBackend()
    _queue(backend, 1, hand_off("engineer", "Header still unverified.", unverifiable=_OBSTACLE)[1])
    _queue(backend, 2, hand_off("engineer", "Header still unverified.")[1])
    events: list[dict] = []

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Send the configured credential.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2),
        workdir=tmp_path,
        on_event=events.append,
    )

    # The obstacle neither ends nor extends the loop by itself.
    assert status == "max_rounds"
    assert [row.review.status for row in rounds] == ["continue", "continue"]
    reviews = [e for e in events if e.get("type") == "round.review.completed"]
    assert reviews[0]["verification_obstacle"] == _OBSTACLE
    assert "verification_obstacle" not in reviews[1]
    assert not [e for e in events if e.get("type") == "round.stall"]
    for event in reviews:
        assert validate_event_envelope(event).errors == ()


def test_both_roles_learn_that_a_masked_display_is_unobservable() -> None:
    from argus.core.model_visible_text import (
        MASKED_DISPLAY_ENGINEER_RULE,
        REVIEW_EVIDENCE_RULE,
    )
    from argus.roles.prompts.engineer import build_mission_prompt

    reviewer = Reviewer(runner=None, skill_store=None)
    review_prompt = reviewer._build_prompt(
        objective="Send the configured credential.", operator_messages=[],
        planner_review_instruction="", round_index=1, session_id=None,
        main_summary="done", main_error=None, prior_checkpoint={},
    )
    assert REVIEW_EVIDENCE_RULE in review_prompt
    assert "display masking" in REVIEW_EVIDENCE_RULE and "rerunnable assertion" in REVIEW_EVIDENCE_RULE

    for include_static in (True, False):
        engineer_prompt = build_mission_prompt(
            task="Send the configured credential.", skill_text="",
            next_action="The header is shown as ******.",
            include_static=include_static,
        )
        assert MASKED_DISPLAY_ENGINEER_RULE in engineer_prompt
    assert "rerunnable test" in MASKED_DISPLAY_ENGINEER_RULE
