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

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import LoopOutcome, ReviewDecision, RoundRecord, RunnerResult
from argus.engineer.round_config import QUESTION_POLICY, SupervisedConfig
from argus.engineer.round_settlement import RoundSettlementMixin, _apply_operator_question_policy
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
        raw_engineer_message="e", engineer_message="e",
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


def test_forbidding_questions_keeps_the_reviewers_words_and_records_the_intervention(
    tmp_path: Path,
) -> None:
    review = ReviewDecision(
        status="blocked",
        reason="parse_date drops the timezone; the CSV writer leaves a trailing comma.",
        next_action="Fix parse_date to keep the timezone and remove the trailing comma.",
        operator_question="May I change the CSV schema?",
        operator_options=[{"id": "yes", "label": "Yes"}],
    )
    review.reviewer_authored = True
    state = RoundLoopState()

    control = _settle(state, review, SupervisedConfig(operator_questions_allowed=False), tmp_path)

    recorded = state.rounds[-1].review
    assert (recorded.status, recorded.reason, recorded.next_action) == (
        review.status, review.reason, review.next_action,
    )
    assert recorded.operator_question == "May I change the CSV schema?"
    assert recorded.operator_options == [{"id": "yes", "label": "Yes"}]
    assert recorded.review_source == "reviewer" and recorded.reviewer_authored
    [intervention] = recorded.interventions
    assert intervention["policy"] == QUESTION_POLICY
    assert intervention["action"] == "continue"
    assert intervention["at"] > 0 and intervention["note"]
    # The work goes on from the Reviewer's repairs, without asking.
    assert control.action == "continue_loop"
    assert "Fix parse_date" in state.reviewer_next_action
    assert "do not ask the operator" in state.reviewer_next_action


def test_a_blocked_review_in_no_operator_mode_keeps_its_verdict_and_the_work_goes_on(
    tmp_path: Path,
) -> None:
    from argus.engineer.runner import EngineerConfig, SupervisedEngineer
    from argus.reviewer import Reviewer, ReviewerConfig

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Wrote the exporter."))
    backend.queue("reviewer", CannedResponse(review_action=(
        "request_review_decision",
        {"review": "The export format is not specified.", "question": "CSV or JSON?"},
    )))
    backend.queue("engineer-r2", CannedResponse(message="Chose CSV and recorded the assumption."))
    backend.queue("reviewer", CannedResponse(review_action=(
        "approve_review", {"review": "The CSV exporter is verified."},
    )))
    engineer = SupervisedEngineer(
        engineer_runner=backend, reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
    )

    status, rounds, _final, _reason, _thread = engineer.run(
        objective="Export the report.",
        engineer_prompt_builder=lambda _next, _static=True: "Export it.",
        supervised_config=SupervisedConfig(operator_questions_allowed=False),
        workdir=tmp_path,
    )

    assert status == "done"
    first = rounds[0].review
    assert first.status == "blocked"
    assert first.operator_question == "CSV or JSON?"
    assert [row["policy"] for row in first.interventions] == [QUESTION_POLICY]
    assert first.interventions[0]["action"] == "continue"


def test_a_placeholder_between_forbidden_questions_hands_the_second_to_the_manager(
    tmp_path: Path,
) -> None:
    config = SupervisedConfig(operator_questions_allowed=False)
    state = RoundLoopState()
    first = ReviewDecision(
        status="continue", reason="needs schema", next_action="ask", operator_question="Schema?",
    )
    state.rounds.append(RoundRecord(
        round_index=1, engineer_message="e1", engineer_exit_code=0,
        review=_apply_operator_question_policy(first, supervised_config=config, state=state),
    ))
    state.rounds.append(RoundRecord(
        round_index=2, engineer_message="e2", engineer_exit_code=1,
        review=provider_turn_cap_review_decision(
            fatal_error="cap", exit_code=1, wind_down_summary="", streak=1, streak_limit=0,
        ),
    ))
    second = ReviewDecision(
        status="continue", reason="still needs schema", next_action="ask", operator_question="Schema?",
    )

    control = _settle(state, second, config, tmp_path)

    assert state.rounds[-1].review.status == "continue"
    assert state.rounds[-1].review.intervention(QUESTION_POLICY)["action"] == "manager"
    assert control.action == "return"
    assert control.terminal[0] == "blocked"
    assert "the Manager decides" in control.terminal[3]
