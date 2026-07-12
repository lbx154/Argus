"""Operator-abort interrupt: the Manager (running in the operator's REPL, a
separate process from the daemon) can decide, mid-mission, that *this one*
backlog item should stop right now — without tearing down the daemon
process itself (contrast with ``fatal_error_looks_like_daemon_stop_request``,
which is a full daemon-shutdown interrupt).

The signal travels as a small file in the shared ``life_dir`` (see
``argus_skill.tools.mission_control``); the running round's
``external_interrupt_reason_provider`` watchdog consumes it and the engineer
subprocess is terminated with ``fatal_error="External interrupt: operator
abort requested: <reason>"``. This must be classified as its OWN category —
never as a retryable backend failure, and never silently laundered into an
infinite retry loop.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    fatal_error_looks_like_backend_failure,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_operator_abort_request,
    operator_abort_review_decision,
)
from argus_skill.reviewer import ReviewerConfig

# --------------------------------------------------------------------------- #
# Pure predicate: distinct from both "normal backend failure" and "daemon stop"
# --------------------------------------------------------------------------- #


def test_operator_abort_pattern_is_recognized() -> None:
    assert fatal_error_looks_like_operator_abort_request(
        "External interrupt: operator abort requested: operator asked to stop"
    )
    # Case-insensitive, like the sibling daemon-stop predicate.
    assert fatal_error_looks_like_operator_abort_request(
        "EXTERNAL INTERRUPT: OPERATOR ABORT REQUESTED: whatever"
    )


def test_operator_abort_pattern_rejects_unrelated_or_empty_text() -> None:
    assert not fatal_error_looks_like_operator_abort_request(None)
    assert not fatal_error_looks_like_operator_abort_request("")
    assert not fatal_error_looks_like_operator_abort_request(
        "External interrupt: daemon stop requested"
    )
    assert not fatal_error_looks_like_operator_abort_request(
        "Process exited with code 1 before turn completion."
    )


def test_operator_abort_and_daemon_stop_are_mutually_exclusive() -> None:
    # The two "intentional interrupt" categories must never both match the
    # same fatal_error string — each one owns a distinct exact prefix.
    daemon_stop = "External interrupt: daemon stop requested"
    operator_abort = "External interrupt: operator abort requested: bye"
    assert fatal_error_looks_like_daemon_stop_request(daemon_stop)
    assert not fatal_error_looks_like_operator_abort_request(daemon_stop)
    assert fatal_error_looks_like_operator_abort_request(operator_abort)
    assert not fatal_error_looks_like_daemon_stop_request(operator_abort)


def test_operator_abort_is_never_misclassified_as_backend_failure() -> None:
    # An intentional operator-driven stop must not count toward the
    # backend_failure_streak (which would falsely blame the backend/model
    # and could — at threshold — behave differently from an intentional
    # blocked-with-clean-reason outcome).
    assert not fatal_error_looks_like_backend_failure(
        "External interrupt: operator abort requested: operator asked to stop"
    )


def test_operator_abort_review_decision_is_honest_daemon_keeps_running() -> None:
    decision = operator_abort_review_decision(
        fatal_error="External interrupt: operator abort requested: stop now",
        exit_code=0,
    )
    assert decision.status == "blocked"
    assert decision.failure_cause == "operator_interrupt"
    # Must NOT claim the daemon itself is restarting/shutting down — only
    # this one mission was interrupted (regression guard against copy-pasting
    # daemon_stop_review_decision's "restart the daemon" wording verbatim,
    # which would misinform the operator that the whole daemon is bouncing).
    assert "daemon" not in decision.reason.lower()
    assert "restart the daemon" not in decision.next_action.lower()
    assert "daemon process itself keeps running" in decision.next_action.lower()
    assert "next" in decision.next_action.lower() or "ready backlog" in decision.next_action.lower()


# --------------------------------------------------------------------------- #
# Round-loop integration: the engineer subprocess was killed mid-turn by the
# watchdog because the Manager requested an abort.
# --------------------------------------------------------------------------- #


class _AbortedEngineerRunner:
    """An engineer whose subprocess the watchdog killed via the abort signal."""

    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, **_kwargs):
        self.calls += 1
        return RunnerResult(
            exit_code=1,
            agent_messages=[],
            thread_id="t1",
            fatal_error=(
                "External interrupt: operator abort requested: "
                "operator asked to stop the running mission"
            ),
        )


class _ExplodingReviewer:
    """Reviewer that must never be called when the engineer was aborted."""

    def evaluate(self, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError(
            "reviewer must be skipped when the engineer was operator-aborted"
        )


class _SuccessfulEngineerRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, **_kwargs):
        self.calls += 1
        return RunnerResult(
            exit_code=0,
            agent_messages=["implemented and verified"],
            thread_id="engineer-thread",
            turn_completed=True,
        )


class _ReviewerAbortedByOperator:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        fatal = (
            "External interrupt: operator abort requested: "
            "operator requested: 停止现在的任务"
        )
        return ReviewDecision(
            status="blocked",
            reason=(
                "Reviewer backend returned no output "
                f"(exit=-15, fatal_error={fatal})."
            ),
            next_action="",
            failure_cause="environmental",
            backend_unavailable=True,
            backend_fatal_error=fatal,
            backend_exit_code=-15,
        )


def test_loop_stops_clean_on_operator_abort_without_calling_reviewer(
    tmp_path: Path,
) -> None:
    events: list[dict] = []
    engineer = _AbortedEngineerRunner()
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=_ExplodingReviewer(),
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=10,
        backend_failure_threshold=2,
        backend_failure_backoff_seconds=0.0,
        effective_progress_timeout_seconds=0,
        background_subagent_advisory=False,
    )
    status, rounds, _final_msg, reason, _tid = engine.run(
        objective="minimize val_bpb",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )

    # Ends the mission cleanly (not an infinite retry, not a crash) — exactly
    # one round, engineer called exactly once, reviewer never invoked.
    assert status == "error"
    assert engineer.calls == 1
    assert len(rounds) == 1
    assert rounds[0].review.failure_cause == "operator_interrupt"
    assert "operator" in reason.lower() or "abort" in reason.lower()

    skipped = [
        e
        for e in events
        if e.get("type") == "round.review.completed" and e.get("review_skipped")
    ]
    assert len(skipped) == 1
    assert "operator abort requested" in skipped[0].get("text", "")


def test_loop_stops_without_backend_retry_when_reviewer_is_operator_aborted(
    tmp_path: Path,
) -> None:
    events: list[dict] = []
    engineer = _SuccessfulEngineerRunner()
    reviewer = _ReviewerAbortedByOperator()
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=10,
        backend_failure_threshold=2,
        backend_failure_backoff_seconds=0.0,
        effective_progress_timeout_seconds=0,
        background_subagent_advisory=False,
    )

    status, rounds, _final_msg, reason, _tid = engine.run(
        objective="finish the game",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "work",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "error"
    assert engineer.calls == 1
    assert reviewer.calls == 1
    assert len(rounds) == 1
    assert rounds[0].review.failure_cause == "operator_interrupt"
    assert "operator" in reason.lower() or "abort" in reason.lower()
    assert not any(
        event.get("type") == "round.reviewer_backend_failure"
        for event in events
    )
    skipped = [
        event
        for event in events
        if event.get("type") == "round.review.completed"
        and event.get("review_skipped")
    ]
    assert len(skipped) == 1
    assert "operator abort requested" in skipped[0].get("text", "")
