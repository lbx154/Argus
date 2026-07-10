"""Regression: a DEAD reviewer backend must FAIL LOUD, never be laundered into a
silent ``continue``.

On 2026-06-25 a refactor ``git mv``'d ``reviewer_schema.json`` out from under a
running daemon. The daemon held the import-time schema path in memory, so every
reviewer round handed codex a now-missing ``--output-schema`` file; codex exited
1 "before turn completion". The reviewer's no-output branch returned
``status="continue"``, so the supervised loop ran the SOLE
completion gate BLIND for ~1.5h (27 rounds, ~$8) with no real review — the
opposite of the operator's "reviewer is the single source of truth" contract.

The contract pinned here:
  * reviewer backend death   -> status="blocked", backend_unavailable=True
                                (NOT "continue")
  * missing output-schema    -> same verdict, detected up front WITHOUT spawning
                                codex
  * the supervised loop       -> escalates to "error" + an operator_alert event
                                once the reviewer-backend failure streak hits the
                                threshold, instead of looping blind.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


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
        checks=[],
        config=ReviewerConfig(model="gpt-5.5"),
    )


def test_backend_death_is_blocked_not_continue() -> None:
    decision = _evaluate(Reviewer(runner=_DeadRunner()))
    # The regression: this branch returned status="continue" before the fix,
    # which let the loop run the completion gate blind. It MUST be a loud,
    # non-verdict block carrying the explicit infra-death marker.
    assert decision.status == "blocked"
    assert decision.status != "continue"
    assert decision.backend_unavailable is True
    assert decision.failure_cause == "environmental"


def test_missing_output_schema_is_blocked_without_spawning_codex() -> None:
    class _ExplodingRunner:
        def run_exec(self, **_kwargs):  # pragma: no cover - must never run
            raise AssertionError(
                "runner must not be invoked when the output-schema is missing"
            )

    reviewer = Reviewer(runner=_ExplodingRunner())
    reviewer.schema_path = "/nonexistent/argus/reviewer_schema.json"
    decision = _evaluate(reviewer)
    assert decision.status == "blocked"
    assert decision.backend_unavailable is True
    assert decision.failure_cause == "environmental"
    assert "missing" in decision.reason.lower()


def test_empty_clean_output_stays_continue() -> None:
    # A clean exit (exit_code==0, no fatal) with empty output is a MODEL-quality
    # miss, NOT infra death: it must stay "continue" and NOT trip the backend
    # escalation path (otherwise a flaky empty turn would falsely fail the loop).
    class _EmptyCleanResult:
        agent_messages: list[str] = []
        exit_code = 0
        fatal_error = ""
        input_tokens = cached_input_tokens = output_tokens = 0

    class _EmptyRunner:
        def run_exec(self, **_kwargs):
            return _EmptyCleanResult()

    decision = _evaluate(Reviewer(runner=_EmptyRunner()))
    assert decision.status == "continue"
    assert decision.backend_unavailable is False


def test_unavailable_engineer_model_blocks_once_with_actionable_error(
    tmp_path: Path,
) -> None:
    events: list[dict] = []

    class _UnavailableModelEngineer:
        calls = 0

        def run_exec(self, **_kwargs):
            self.calls += 1
            return RunnerResult(
                exit_code=0,
                agent_messages=[],
                fatal_error='Error: Model "gpt5.6" from --model flag is not available.',
            )

    class _ReviewerMustNotRun:
        def evaluate(self, **_kwargs):  # pragma: no cover - contract assertion
            raise AssertionError("Reviewer must not run when Engineer model is invalid")

    engineer_runner = _UnavailableModelEngineer()
    engine = SupervisedEngineer(
        engineer_runner=engineer_runner,
        reviewer=_ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="gpt5.6"),
        reviewer_config=ReviewerConfig(model="gpt5.6"),
    )
    status, rounds, _message, reason, _tid = engine.run(
        objective="prove a theorem",
        engineer_prompt_builder=lambda _next, _static=True: "prove it",
        supervised_config=SupervisedConfig(
            max_rounds=10,
            backend_failure_backoff_seconds=0,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "blocked"
    assert engineer_runner.calls == 1
    assert len(rounds) == 1
    assert "model is unavailable" in reason.lower()
    alerts = [event for event in events if event.get("type") == "round.model_configuration_error"]
    assert len(alerts) == 1 and alerts[0]["operator_alert"] is True


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
            failure_cause="environmental",
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

    # Must FAIL LOUD at the threshold — never run blind to max_rounds.
    assert status == "error"
    # F8: the reviewer flake retries the REVIEWER in place — it must NOT re-run
    # the (xhigh) engineer turn. So the engineer ran once, the reviewer twice,
    # and only ONE round record was banked (not one per reviewer flake).
    assert engineer.calls == 1
    assert reviewer.calls == 2  # retried up to backend_failure_threshold
    assert len(rounds) == 1
    assert "reviewer backend unavailable" in reason.lower()

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


def test_loop_recovers_when_reviewer_backend_comes_back(tmp_path: Path) -> None:
    # A SINGLE transient reviewer-backend blip (streak < threshold) must be
    # tolerated with a backoff retry, then a real verdict resets the streak —
    # the loop keeps going rather than dying on one flake.
    events: list[dict] = []

    class _FlakyThenDoneReviewer:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, **_kwargs) -> ReviewDecision:
            self.calls += 1
            if self.calls == 1:
                return ReviewDecision(
                    status="blocked",
                    reason="transient backend blip",
                    next_action="retry",
                    failure_cause="environmental",
                    backend_unavailable=True,
                )
            return ReviewDecision(
                status="done",
                reason="objective met",
                next_action="none",
            )

    engineer = _HealthyEngineerRunner()
    reviewer = _FlakyThenDoneReviewer()
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
    status, rounds, _final_msg, _reason, _tid = engine.run(
        objective="minimize val_bpb",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )
    assert status == "done"
    # F8: the transient reviewer blip retried the REVIEWER in place; the engineer
    # ran exactly once and its single output was reused for the real verdict.
    assert engineer.calls == 1
    assert reviewer.calls == 2  # blip, then the real "done" verdict
    assert len(rounds) == 1


def test_reviewer_flake_does_not_rerun_engineer(tmp_path: Path) -> None:
    # The exact F8 contract: a reviewer infra flake reuses the engineer output
    # already in hand; the round record stores that ONE engineer turn's message,
    # never a second (xhigh) engineer turn's output.
    class _FlakyThenContinueReviewer:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, **_kwargs) -> ReviewDecision:
            self.calls += 1
            if self.calls == 1:
                return ReviewDecision(
                    status="blocked",
                    reason="transient backend blip",
                    next_action="retry",
                    failure_cause="environmental",
                    backend_unavailable=True,
                )
            return ReviewDecision(status="done", reason="ok", next_action="none")

    engineer = _HealthyEngineerRunner()
    reviewer = _FlakyThenContinueReviewer()
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
    status, rounds, _final_msg, _reason, _tid = engine.run(
        objective="minimize val_bpb",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=lambda _e: None,
    )
    assert status == "done"
    assert engineer.calls == 1  # NOT re-run for the reviewer flake
    assert len(rounds) == 1
    # The single engineer turn's output (#1) is what got reviewed — not a
    # discarded-and-regenerated second turn.
    assert "increment #1" in rounds[0].engineer_message
