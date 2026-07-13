from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import ReviewerConfig


class _StoppedEngineer:
    def __init__(self, stop_kind: str) -> None:
        self.stop_kind = stop_kind
        self.calls = 0

    def run_exec(self, **_kwargs) -> RunnerResult:
        self.calls += 1
        return RunnerResult(
            exit_code=-1,
            fatal_error=f"stopped: {self.stop_kind}",
            stop_kind=self.stop_kind,  # type: ignore[arg-type]
        )


class _ReviewerMustNotRun:
    def evaluate(self, **_kwargs):  # pragma: no cover - contract assertion
        raise AssertionError("reviewer must not run after an external stop")


def _run_engineer(
    tmp_path: Path,
    stop_kind: str,
) -> tuple[str, _StoppedEngineer, list[dict]]:
    events: list[dict] = []
    backend = _StoppedEngineer(stop_kind)
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=_ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )
    status, _rounds, _message, _reason, _thread = engine.run(
        objective="test stop handling",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )
    return status, backend, events


@pytest.mark.parametrize(
    ("stop_kind", "expected_status"),
    [
        ("budget_exhausted", "paused_budget"),
        ("provider_cooldown", "paused_provider_cooldown"),
        ("provider_fence", "paused_provider_fence"),
    ],
)
def test_external_stops_do_not_enter_backend_failure_retry(
    tmp_path: Path,
    stop_kind: str,
    expected_status: str,
) -> None:
    status, backend, events = _run_engineer(tmp_path, stop_kind)

    assert status == expected_status
    assert backend.calls == 1
    assert not [
        event
        for event in events
        if event.get("type") == "round.backend_failure.backoff"
    ]


def test_backend_unavailable_keeps_existing_retry_policy(tmp_path: Path) -> None:
    status, backend, _events = _run_engineer(tmp_path, "backend_unavailable")

    assert status == "error"
    assert backend.calls == 2


def test_provider_max_budget_is_a_fence_not_backend_failure() -> None:
    assert _raw_backend_stop_kind(
        fatal_error="Claude runner reported error_max_budget_usd.",
        exit_code=1,
    ) == "provider_fence"


def test_reviewer_budget_stop_pauses_without_failure_streak(tmp_path: Path) -> None:
    events: list[dict] = []

    class _HealthyEngineer:
        def run_exec(self, **_kwargs) -> RunnerResult:
            return RunnerResult(exit_code=0, agent_messages=["work landed"])

    class _BudgetStoppedReviewer:
        calls = 0

        def evaluate(self, **_kwargs) -> ReviewDecision:
            self.calls += 1
            return ReviewDecision(
                status="blocked",
                reason="review call denied by mission budget",
                next_action="resume after replenishing budget",
                backend_unavailable=True,
                backend_stop_kind="budget_exhausted",
            )

    reviewer = _BudgetStoppedReviewer()
    engine = SupervisedEngineer(
        engineer_runner=_HealthyEngineer(),
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )
    status, _rounds, _message, _reason, _thread = engine.run(
        objective="review this",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "paused_budget"
    assert reviewer.calls == 1
    assert not [
        event
        for event in events
        if event.get("type") == "round.reviewer_backend_failure"
    ]
