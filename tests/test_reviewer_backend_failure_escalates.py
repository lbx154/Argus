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

import pytest

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


# --------------------------------------------------------------------------- #
# Loop-level contract: streak of reviewer-backend deaths escalates to "error"
# --------------------------------------------------------------------------- #
class _HealthyEngineerRunner:
    """Engineer always succeeds, so each round reaches the reviewer call."""

    def run_exec(self, **_kwargs):
        return RunnerResult(
            exit_code=0,
            agent_messages=["implemented the next increment"],
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
    engine = SupervisedEngineer(
        engineer_runner=_HealthyEngineerRunner(),
        reviewer=_DeadBackendReviewer(),
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
        engineer_prompt_builder=lambda _next_action: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )

    # Must FAIL LOUD at the threshold — never run blind to max_rounds.
    assert status == "error"
    assert len(rounds) == 2  # stopped exactly at backend_failure_threshold
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

    engine = SupervisedEngineer(
        engineer_runner=_HealthyEngineerRunner(),
        reviewer=_FlakyThenDoneReviewer(),
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
        engineer_prompt_builder=lambda _next_action: "do the next increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=events.append,
    )
    assert status == "done"
    # round 1 = transient blip (retry), round 2 = real done verdict.
    assert len(rounds) == 2
