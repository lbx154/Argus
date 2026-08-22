"""Small round budgets must retain the guards that can meaningfully fire.

The semantic-stall guard is driven only by the Reviewer's structured
``FORWARD_PROGRESS=false`` judgment. These tests exercise the real
Engineer -> Reviewer -> settlement path so arithmetic-only tests cannot hide a
disconnected runtime counter again.
"""

from __future__ import annotations

import json

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer import round_settlement
from argus_skill.engineer.round_state import EngineerTurnOutcome, RoundLoopState
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _review_json(
    status: str,
    *,
    forward_progress: bool | None,
    reason: str = "Residual still open.",
) -> str:
    payload: dict[str, object] = {
        "status": status,
        "reason": reason,
        "next_action": "Discharge the next conjunct." if status == "continue" else "",
        "operator_question": None,
    }
    if forward_progress is not None:
        payload["planner_report"] = {
            "forward_progress": forward_progress,
            "plan_signal": "continue",
        }
    return json.dumps(payload)


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _queue_round(
    backend: MemoryBackend,
    round_index: int,
    *,
    status: str = "continue",
    forward_progress: bool | None = False,
) -> None:
    backend.queue(
        f"engineer-r{round_index}",
        CannedResponse(message=f"round {round_index} produced a substantive result"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(
            message=_review_json(
                status,
                forward_progress=forward_progress,
                reason=(
                    "The objective is verified complete."
                    if status == "done"
                    else "Residual still open."
                ),
            )
        ),
    )


def test_default_budget_leaves_every_guard_untouched() -> None:
    config = SupervisedConfig()

    assert config.max_rounds == 32
    assert config.stall_threshold == 4
    assert config.soft_round_limit == 12
    assert config.hard_escalate_rounds == 24


def test_three_round_budget_rescales_guards_into_reach() -> None:
    config = SupervisedConfig(max_rounds=3)

    assert config.stall_threshold == 2
    assert config.soft_round_limit == 2
    assert config.hard_escalate_rounds == 3


def test_explicit_no_progress_reaches_stall_guard_on_real_bounded_run(
    tmp_path,
) -> None:
    backend = MemoryBackend()
    _queue_round(backend, 1, forward_progress=False)
    _queue_round(backend, 2, forward_progress=False)
    events: list[dict] = []

    status, rounds, _final, reason, _thread = _engineer(backend).run(
        objective="Discharge the bounded proof node.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            decision_progress_timeout_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "no_progress"
    assert len(rounds) == 2
    assert "Reviewer reported no forward progress for 2 consecutive rounds" in reason
    # A stall measures the approach, not the question. When the reason said only
    # that the rounds had ended, one campaign read it as a verdict on the work:
    # its claim-bearing benchmark run stalled and the Planner's next task was an
    # unrelated question carrying a four-word objective.
    assert "The rounds ended; the question did not" in reason
    assert "changing the question is how a campaign loses its paper" in reason
    stall_events = [event for event in events if event.get("type") == "round.stall"]
    assert [event["semantic_stall_streak"] for event in stall_events] == [1, 2]


def test_explicit_no_progress_keeps_decision_timeout_clock(
    monkeypatch,
    tmp_path,
) -> None:
    state = RoundLoopState(last_decision_progress_at=100.0)
    outcome = EngineerTurnOutcome(
        engineer_result=RunnerResult(exit_code=0),
        round_thread_id=None,
        fatal_error=None,
        safe_fatal_error=None,
        stop_kind=None,
        raw_engineer_message="substantive work",
        engineer_message="substantive work",
        process_ownership_note="",
        round_started_at=99.0,
    )
    monkeypatch.setattr(round_settlement.time, "monotonic", lambda: 1900.0)

    control = _engineer(MemoryBackend())._settle_round(
        review=ReviewDecision(
            status="continue",
            reason="No forward progress.",
            next_action="Try a different mechanism.",
            planner_report={"forward_progress": False},
        ),
        round_index=1,
        supervised_config=SupervisedConfig(
            max_rounds=3,
            decision_progress_timeout_seconds=1800,
        ),
        workdir=tmp_path,
        outcome=outcome,
        state=state,
        review_completed_hook=None,
        continue_adaptor=None,
        on_event=None,
    )

    assert control.terminal is not None
    assert control.terminal[0] == "no_progress"
    assert "1800 seconds without decision progress" in control.terminal[3]


def test_explicit_progress_continues_beyond_three_rounds(tmp_path) -> None:
    backend = MemoryBackend()
    _queue_round(backend, 1, forward_progress=False)
    _queue_round(backend, 2, forward_progress=True)
    _queue_round(backend, 3, forward_progress=False)
    _queue_round(backend, 4, forward_progress=True)
    _queue_round(backend, 5, status="done", forward_progress=True)

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Converge one residual at a time.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=5,
            stall_threshold=2,
            soft_round_limit=0,
            hard_escalate_rounds=0,
            decision_progress_timeout_seconds=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == 5


def test_productive_mission_crosses_round_24_with_a_local_regression(
    tmp_path,
) -> None:
    backend = MemoryBackend()
    for round_index in range(1, 27):
        _queue_round(
            backend,
            round_index,
            status="done" if round_index == 26 else "continue",
            forward_progress=(False if round_index == 24 else True),
        )
    events: list[dict] = []

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Keep one coherent long-horizon mission while evidence improves.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=26,
            stall_threshold=3,
            soft_round_limit=0,
            hard_escalate_rounds=24,
            decision_progress_timeout_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert len(rounds) == 26
    review_24 = next(
        event
        for event in events
        if event.get("type") == "round.review.completed"
        and event.get("round_index") == 24
    )
    assert review_24["forward_progress"] is False
    assert review_24["plan_signal"] == "continue"
    boundary = [
        event
        for event in events
        if event.get("type") == "round.escalated"
        and event.get("round_index") == 24
    ]
    assert boundary == [{
        "type": "round.escalated",
        "round_index": 24,
        "hard_escalate_rounds": 24,
        "forward_progress": False,
        "continuation_reason": "bounded_no_progress_observation",
        "text": (
            "round 24 crossed the continuation boundary under the Reviewer's "
            "explicit progress judgment"
        ),
    }]


def test_hard_boundary_refuses_blind_continue_without_progress_signal() -> None:
    status, reason = _engineer(MemoryBackend())._classify(
        review=ReviewDecision(
            status="continue",
            reason="More work remains.",
            next_action="Continue.",
            planner_report={},
        ),
        no_progress_streak=0,
        no_progress_threshold=2,
        semantic_stall_streak=0,
        stall_threshold=4,
        round_index=24,
        max_rounds=500,
        hard_escalate_rounds=24,
    )

    assert status == "blocked"
    assert "did not provide an explicit forward-progress judgment" in reason


def test_missing_progress_signal_never_counts_as_stall_evidence(tmp_path) -> None:
    backend = MemoryBackend()
    _queue_round(backend, 1, forward_progress=False)
    _queue_round(backend, 2, forward_progress=None)
    _queue_round(backend, 3, forward_progress=False)
    _queue_round(backend, 4, status="done", forward_progress=True)

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Do not infer progress from Reviewer prose.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            stall_threshold=2,
            soft_round_limit=0,
            hard_escalate_rounds=0,
            decision_progress_timeout_seconds=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == 4


def test_two_round_budget_does_not_invent_a_one_strike_stall_policy() -> None:
    config = SupervisedConfig(max_rounds=2)

    assert config.stall_threshold == 0
    assert SupervisedConfig(max_rounds=2, stall_threshold=1).stall_threshold == 1


def test_explicitly_disabled_guards_stay_disabled() -> None:
    config = SupervisedConfig(
        max_rounds=2_147_483_647,
        stall_threshold=0,
        soft_round_limit=0,
        hard_escalate_rounds=0,
    )

    assert config.stall_threshold == 0
    assert config.soft_round_limit == 0
    assert config.hard_escalate_rounds == 0


def test_single_round_budget_disables_impossible_stall_guard() -> None:
    config = SupervisedConfig(max_rounds=1)

    # ``_classify`` intentionally lets the final-round result win, so there is
    # no round_index < max_rounds point at which a semantic-stall guard can fire.
    assert config.stall_threshold == 0
    assert config.soft_round_limit == 1
    assert config.hard_escalate_rounds == 1


def test_nonpositive_budget_is_left_alone() -> None:
    config = SupervisedConfig(max_rounds=0)

    assert config.stall_threshold == 4
    assert config.soft_round_limit == 12
    assert config.hard_escalate_rounds == 24
