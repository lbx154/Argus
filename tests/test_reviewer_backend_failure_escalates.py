"""Regression: a DEAD reviewer backend must FAIL LOUD, never be laundered into a
silent ``continue``.

A reviewer transport that exits before the named verdict footer must never be
laundered into ``continue``; that would run the sole completion gate blind.

The contract pinned here:
  * reviewer backend death   -> status="blocked", backend_unavailable=True
                                (NOT "continue")
  * the supervised loop       -> escalates to "error" + an operator_alert event
                                once the reviewer-backend failure streak hits the
                                threshold, instead of looping blind.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus.reviewer import Reviewer, ReviewerConfig


# --------------------------------------------------------------------------- #
# Reviewer-level contract: backend death is a non-verdict block, not a continue
# --------------------------------------------------------------------------- #
class _DeadResult:
    """A RunnerResult from a codex subprocess that died before any turn."""

    agent_messages: list[str] = []
    exit_code = 1
    fatal_error = "Process exited with code 1 before turn completion."
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0


class _DeadRunner:
    def run_exec(self, **_kwargs):
        return _DeadResult()


def _evaluate(reviewer: Reviewer) -> ReviewDecision:
    return reviewer.evaluate(
        objective="minimize val_bpb",
        round_index=1,
        session_id=None,
        main_summary="(engineer handoff)",
        main_error=None,
        config=ReviewerConfig(model="gpt-5.5"),
    )


def test_backend_death_is_blocked_not_continue() -> None:
    decision = _evaluate(Reviewer(runner=_DeadRunner()))
    # The regression: this branch returned status="continue" before the fix,
    # which let the loop run the completion gate blind. It MUST be a loud,
    # non-verdict block carrying the explicit infra-death marker.
    assert decision.status == "manager"
    assert decision.status != "engineer"
    assert decision.backend_unavailable is True


@pytest.mark.parametrize("interrupt,kind,said,said_zh", [
    ("operator pause requested", "operator_pause", "because you paused the work", "因为你暂停了工作"),
    ("operator abort requested: replace the target", "operator_abort",
     "because you cancelled the task", "因为你取消了这项任务"),
    ("daemon stop requested", "daemon_shutdown",
     "because the Argus background service stopped or restarted", "因为 Argus 后台服务停止或重启了"),
    ("Manager turn wall-clock limit reached", "backend_unavailable",
     "because the work was interrupted", "因为工作被打断了"),
])
def test_an_interrupted_review_names_who_stopped_it_only_when_the_record_does(
    interrupt: str, kind: str, said: str, said_zh: str,
) -> None:
    class _InterruptedRunner:
        def run_exec(self, **_kwargs):
            return RunnerResult(exit_code=130, fatal_error=f"External interrupt: {interrupt}")

    decision = _evaluate(Reviewer(runner=_InterruptedRunner()))
    assert decision.reason.startswith(f"The Reviewer could not finish reading this round {said}; ")
    assert "nothing here is a judgment on the work" in decision.reason
    # Who stopped it travels as the decision's stop kind, not only as its sentence.
    assert decision.backend_stop_kind == kind

    decision = Reviewer(runner=_InterruptedRunner()).evaluate(
        objective="降低验证集损失", round_index=1, session_id=None,
        main_summary="(engineer handoff)", main_error=None, config=ReviewerConfig(model="gpt-5.5"),
    )
    assert decision.reason.startswith(f"审阅者没能读完这一轮，{said_zh}；")


@pytest.mark.parametrize(
    "fatal_error",
    [
        'Error: 421 "Misdirected Request"\nError: Failed to load models (Request ID: 1)',
        "Error: Access denied by policy settings (Request ID: 2)",
    ],
)
def test_provider_startup_refusal_pauses_instead_of_consuming_missions(
    tmp_path: Path,
    fatal_error: str,
) -> None:
    """A CLI that cannot reach any model (no catalog, policy denial) is a
    provider outage. On 2026-09-06 such an outage ran every queued mission
    through two failing rounds and settled it as error; the mission must pause
    for cooldown instead so the backlog survives until access returns."""
    events: list[dict] = []

    class _RefusedEngineer:
        calls = 0

        def run_exec(self, **_kwargs):
            self.calls += 1
            return RunnerResult(exit_code=1, agent_messages=[], fatal_error=fatal_error)

    class _ReviewerMustNotRun:
        def evaluate(self, **_kwargs):  # pragma: no cover - contract assertion
            raise AssertionError("Reviewer must not run when the provider refused")

    engineer_runner = _RefusedEngineer()
    engine = SupervisedEngineer(
        engineer_runner=engineer_runner,
        reviewer=_ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="gpt-5.6-sol"),
        reviewer_config=ReviewerConfig(model="gpt-5.6-sol"),
    )
    status, rounds, _message, _reason, _tid = engine.run(
        objective="prove a theorem",
        engineer_prompt_builder=lambda _next, _static=True: "prove it",
        supervised_config=SupervisedConfig(
            max_rounds=10,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "parked"
    assert engineer_runner.calls == 1
    assert rounds[0].stop_kind == "provider_cooldown"


# --------------------------------------------------------------------------- #
# Loop-level contract: streak of reviewer-backend deaths escalates to "error"
# --------------------------------------------------------------------------- #
class _HealthyEngineerRunner:
    """Engineer always succeeds, so each round reaches the reviewer call."""

    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, **_kwargs):
        self.calls += 1
        return RunnerResult(
            exit_code=0,
            agent_messages=[f"implemented increment #{self.calls}"],
            thread_id="t1",
            fatal_error=None,
        )


class _DeadBackendReviewer:
    """Reviewer whose backend is unavailable every round (no verdict)."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs) -> ReviewDecision:
        self.calls += 1
        return ReviewDecision(
            status="blocked",
            reason=(
                "Reviewer backend returned no output (exit=1, fatal_error="
                "Process exited with code 1 before turn completion)."
            ),
            next_action="Retry on a fresh session.",
            backend_unavailable=True,
        )


def test_loop_escalates_to_error_on_reviewer_backend_death(tmp_path: Path) -> None:
    events: list[dict] = []
    engineer = _HealthyEngineerRunner()
    reviewer = _DeadBackendReviewer()
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
    )
    status, rounds, _final_msg, reason, _tid = engine.run(
        objective="minimize val_bpb",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )

    # Must FAIL LOUD at the threshold — never run blind to max_rounds.
    assert status == "error"
    # F8: the reviewer flake retries the REVIEWER in place — it must NOT re-run
    # the (xhigh) engineer turn. So the engineer ran once, the reviewer twice,
    # and only ONE round record was banked (not one per reviewer flake).
    assert engineer.calls == 1
    assert reviewer.calls == 2  # retried up to backend_failure_threshold
    assert len(rounds) == 1
    # The operator reads this sentence when the task stops: plain words,
    # the count of attempts, and the Reviewer's own record after it.
    assert reason.startswith(
        "The Reviewer could not reach a judgment 2 times in a row, so Argus "
        "stopped this task rather than settle the round without a real judgment. "
    )
    assert "Reviewer backend returned no output" in reason

    alerts = [e for e in events if e.get("type") == "round.reviewer_backend_failure"]
    assert len(alerts) == 2
    assert all(e.get("operator_alert") is True for e in alerts)

    # The core regression: the loop must NOT have emitted a single "continue"
    # review verdict while the reviewer backend was dead.
    continues = [
        e
        for e in events
        if e.get("type") == "round.review.completed" and e.get("status") == "continue"
    ]
    assert continues == []


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs) -> ReviewDecision:
        self.calls += 1
        return ReviewDecision(
            status="done",
            reason="watchdog recovery completed",
            next_action="",
        )


class _WatchdogThenHealthyEngineer:
    def __init__(self, *, always_fail: bool = False) -> None:
        self.always_fail = always_fail
        self.calls = 0
        self.resume_thread_ids: list[str | None] = []

    def run_exec(self, **kwargs):
        self.calls += 1
        self.resume_thread_ids.append(kwargs.get("resume_thread_id"))
        if self.always_fail or self.calls == 1:
            return RunnerResult(
                exit_code=-1,
                agent_messages=[],
                # The same named command hangs each time: a repeat, not a
                # new attempt. Unnamed stops are covered in
                # tests/engineer/test_silent_command_limit.py.
                fatal_error=(
                    "Forced restart after hard idle timeout "
                    "(2700s without a model stream event; running tool: sleep 9999)"
                ),
            )
        return RunnerResult(
            exit_code=0,
            agent_messages=["recovered from CHECKPOINT.md"],
            thread_id="fresh-provider-session",
        )


def test_watchdog_timeout_retries_once_from_checkpoint_in_fresh_session(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    checkpoint.write_text("# Current State\nDurable state is present.\n", encoding="utf-8")
    events: list[dict] = []
    prompts: list[tuple[bool, str]] = []
    runner = _WatchdogThenHealthyEngineer()
    reviewer = _DoneReviewer()
    engine = SupervisedEngineer(
        engineer_runner=runner,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )

    status, rounds, _message, _reason, _thread_id = engine.run(
        objective="recover safely",
        engineer_prompt_builder=lambda _next, include_static=True: prompts.append(
            (include_static, "continue")
        )
        or "continue",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
            checkpoint_path=checkpoint,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert runner.calls == 2
    assert runner.resume_thread_ids == [None, None]
    assert [include_static for include_static, _prompt in prompts] == [True, True]
    assert reviewer.calls == 1
    assert len(rounds) == 2
    retry = next(event for event in events if event["type"] == "round.watchdog.retry")
    assert retry["fresh_session"] is True
    assert retry["checkpoint_available"] is True
    assert retry["checkpoint_path"] == str(checkpoint)
