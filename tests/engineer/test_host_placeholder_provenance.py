"""Host-written round records are never read as the Reviewer's judgment, and
a host intervention sits beside the Reviewer's verdict instead of rewriting it.

* A mission that stops because the Reviewer leg never answered is neither
  credited to the Reviewer nor handed to the Manager's stage writer.
* A turn-limit restart adds its note to the Reviewer's pending request
  instead of replacing it.
* Forbidding operator questions keeps the Reviewer's words and records, beside
  them, the policy, what the host did instead of asking, and when.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.core.models import LoopOutcome, ReviewDecision, RunnerResult
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_settlement import RoundSettlementMixin
from argus.engineer.round_state import EngineerTurnOutcome, RoundLoopState
from argus.engineer.round_stop_signals import (
    backend_failure_review_decision,
    idle_termination_review_decision,
    provider_turn_cap_review_decision,
)


def _settle(state: RoundLoopState, review: ReviewDecision, config: SupervisedConfig, workdir: Path):
    outcome = EngineerTurnOutcome(
        engineer_result=SimpleNamespace(exit_code=0, fatal_error=None),
        round_thread_id=None, fatal_error=None, safe_fatal_error=None, stop_kind=None,
        handoff=None, engineer_message="e",
        process_ownership_note="", round_started_at=0.0,
    )
    return RoundSettlementMixin()._settle_round(
        review=review, round_index=len(state.rounds) + 1, supervised_config=config,
        workdir=workdir, outcome=outcome, state=state, review_completed_hook=None,
        on_event=None,
    )


def test_engineer_placeholders_name_themselves_as_the_source() -> None:
    records = (
        backend_failure_review_decision(fatal_error="x", exit_code=1, streak=1, threshold=2),
        idle_termination_review_decision(fatal_error="x", exit_code=1, streak=1, threshold=2),
        provider_turn_cap_review_decision(
            fatal_error="x", exit_code=1, wind_down_summary="", streak=1, streak_limit=0,
        ),
    )
    for review in records:
        assert review.review_source == review.host_placeholder != "reviewer"


class _RaisingReviewer:
    def evaluate(self, **_kwargs):
        raise RuntimeError("reviewer process crashed")


class _OneTurnEngineer:
    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        return RunnerResult(exit_code=0, agent_messages=["changed evaluate.py"], thread_id="thr-e")


def test_a_reviewer_that_never_answered_is_not_credited_or_staged(tmp_path: Path) -> None:
    from argus.apps._runtime import _SkillLoopRunner
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.engineer.runner import EngineerConfig, SupervisedEngineer
    from argus.reviewer import ReviewerConfig

    supervised = SupervisedEngineer.__new__(SupervisedEngineer)
    supervised.engineer_runner = _OneTurnEngineer()
    supervised.engineer_config = EngineerConfig(model="stub")
    supervised.reviewer = _RaisingReviewer()
    supervised.reviewer_config = ReviewerConfig(model="stub")
    status, rounds, final_message, reason, _thread = supervised.run(
        objective="score on the held-out split",
        engineer_prompt_builder=lambda next_action, include_static=True: "WORK",
        supervised_config=SupervisedConfig(
            max_rounds=4, backend_failure_threshold=2, backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
    )
    assert status == "error"
    assert rounds[-1].review.status == "blocked"
    assert rounds[-1].review.review_source == "reviewer_unavailable"

    state = _ExecuteState()
    state.workdir = tmp_path
    state.effective_require_independent_review = True
    state.outcome = LoopOutcome(
        status=status, reason=reason, rounds=rounds, final_message=final_message,
        workdir=str(tmp_path),
    )
    runtime = _SkillLoopRunner.__new__(_SkillLoopRunner)
    runtime._consume_auth_failure = lambda: False
    runtime._set_usage_context = lambda _value: None
    stage_calls: list[bool] = []
    runtime._decide_stage_transition = lambda **_kwargs: stage_calls.append(True) or {"action": "hold"}

    runtime._extract_execute_outcome_fields(state)
    runtime._maybe_decide_stage_transition(
        state, sink=SimpleNamespace(handle_event=lambda _event: None),
        mission_id="m", usage_mission_id="m:attempt:1", maintenance_mission=False,
        skip_stage_transition=False, preplanned=False, stage_closing=True,
    )
    result = runtime._build_execute_outcome(state)

    assert stage_calls == []
    assert result.final_review_source != "reviewer"
    assert result.final_review_status == "blocked"


def test_a_turn_limit_restart_keeps_the_reviewers_pending_request() -> None:
    from argus.engineer.round_execution import RoundExecutionMixin

    class _Session:
        workdir = "."

        def rotate(self, _reason: str) -> None:
            return None

    state = RoundLoopState()
    state.engineer_session = _Session()
    state.reviewer_next_action = "Fix the train-split leak in evaluate.py, then rerun table 2."
    engineer = SimpleNamespace(engineer_config=SimpleNamespace(isolate_workdir=False))

    def restart(message: str) -> None:
        RoundExecutionMixin._handle_provider_turn_cap_restart(
            engineer,
            round_index=len(state.rounds) + 1,
            supervised_config=SupervisedConfig(max_rounds=0),
            outcome=SimpleNamespace(
                engineer_result=RunnerResult(exit_code=-15), round_thread_id=None,
                fatal_error="Provider turn cap reached", engineer_message=message,
            ),
            state=state,
            on_event=None,
        )

    restart("fixed the leak")
    restart("reran half of table 2")

    pending = state.reviewer_next_action or ""
    assert pending.startswith("Fix the train-split leak in evaluate.py")
    assert "reran half of table 2" in pending
    # The second restart's note replaced the first one's.
    assert "fixed the leak" not in pending
    assert pending.count("per-call provider-turn allowance") == 1


def test_a_question_nobody_may_be_asked_still_goes_where_the_reviewer_sent_it(
    tmp_path: Path,
) -> None:
    """``→ operator`` parks the work; with no operator, the Manager decides the card."""
    review = ReviewDecision(
        status="blocked",
        reason="parse_date drops the timezone; the CSV writer leaves a trailing comma.",
        next_action="Fix parse_date to keep the timezone and remove the trailing comma.",
        operator_question="May I change the CSV schema?",
    )
    review.reviewer_authored = True
    state = RoundLoopState()

    control = _settle(state, review, SupervisedConfig(operator_questions_allowed=False), tmp_path)

    recorded = state.rounds[-1].review
    assert recorded == review and recorded.interventions == []
    assert control.action == "return"
    assert control.terminal[0] == "blocked"
