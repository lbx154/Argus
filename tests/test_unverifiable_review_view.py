"""A disputed fact the Reviewer cannot observe reaches the Manager by name.

A credential-building line can be masked in every role's view. A Reviewer
that cannot see the line repeats its finding and an Engineer that cannot see it
repeats its fix; more rounds cannot change that. The Reviewer may say so
through its native action, and the loop then labels the stall apart from an
ordinary lack of progress so the Manager can steer. Nothing here decides the
review: the status, the stall counter and the round budget are unchanged.
"""

from __future__ import annotations

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.event_catalog import validate_event_envelope
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.manager.supervision import _prompt as manager_prompt
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import ReviewActions

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
    backend.queue("reviewer", CannedResponse(review_action=("revise_review", payload)))


def test_only_continuing_actions_offer_the_obstacle_field() -> None:
    tools = {tool["name"]: tool for tool in ReviewActions().tools}

    for name in ("revise_review", "defer_review"):
        assert "unverifiable" in tools[name]["inputSchema"]["properties"]
    for name in ("approve_review", "request_review_decision", "replan_review"):
        assert "unverifiable" not in tools[name]["inputSchema"]["properties"]


def test_reviewer_obstacle_is_carried_on_review_and_stall_events(tmp_path) -> None:
    backend = MemoryBackend()
    _queue(backend, 1, {"review": "Header still unverified.", "forward_progress": False,
                        "unverifiable": _OBSTACLE})
    _queue(backend, 2, {"review": "Header still unverified.", "forward_progress": False})
    events: list[dict] = []

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Send the configured credential.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        # max_rounds=0 is the unbounded default the incident ran under.
        supervised_config=SupervisedConfig(max_rounds=0, stall_threshold=2,
                                           decision_progress_timeout_seconds=0),
        workdir=tmp_path,
        on_event=events.append,
    )

    # The obstacle neither ends nor extends the loop by itself.
    assert status == "no_progress"
    assert [row.review.status for row in rounds] == ["continue", "continue"]
    reviews = [e for e in events if e.get("type") == "round.review.completed"]
    assert reviews[0]["verification_obstacle"] == _OBSTACLE
    assert "verification_obstacle" not in reviews[1]
    stalls = [e for e in events if e.get("type") == "round.stall"]
    assert [e["stall_reason"] for e in stalls] == [
        "unverifiable_through_view", "no_forward_progress",
    ]
    for event in (*reviews, *stalls):
        assert event["round_max"] == 0
        assert validate_event_envelope(event).errors == ()


def test_manager_supervision_failure_codes_validate() -> None:
    for code in ("decision_incomplete", "evidence_uncited", "superseded"):
        event = {"type": "life.manager.supervision.failed", "error_code": code}
        assert not any("error_code" in error for error in validate_event_envelope(event).errors)


def test_manager_is_told_what_an_obstacle_means() -> None:
    class _Observation:
        def render(self) -> str:
            return "{}"

    text = manager_prompt(_Observation())
    assert "verification_obstacle" in text
    assert "rerunnable check" in text


def test_both_roles_learn_that_a_masked_display_is_unobservable() -> None:
    from argus.core.model_visible_text import (
        MASKED_DISPLAY_ENGINEER_RULE,
        MASKED_DISPLAY_REVIEW_RULE,
    )
    from argus.roles.prompts.engineer import build_mission_prompt

    reviewer = Reviewer(runner=None, skill_store=None)
    review_prompt = reviewer._build_prompt(
        objective="Send the configured credential.", operator_messages=[],
        planner_review_instruction="", round_index=1, session_id=None,
        main_summary="done", main_error=None, prior_checkpoint={},
    )
    assert MASKED_DISPLAY_REVIEW_RULE in review_prompt
    assert "rerunnable assertion" in MASKED_DISPLAY_REVIEW_RULE

    for include_static in (True, False):
        engineer_prompt = build_mission_prompt(
            task="Send the configured credential.", skill_text="",
            next_action="The header is shown as ******.",
            include_static=include_static,
        )
        assert MASKED_DISPLAY_ENGINEER_RULE in engineer_prompt
    assert "rerunnable test" in MASKED_DISPLAY_ENGINEER_RULE
