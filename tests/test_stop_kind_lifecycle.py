from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical


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
        ("daemon_shutdown", "paused_daemon_shutdown"),
        ("operator_pause", "paused_operator"),
        ("operator_abort", "aborted"),
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


@pytest.mark.parametrize(
    ("fatal_error", "expected"),
    [
        ("External interrupt: daemon stop requested", "daemon_shutdown"),
        ("External interrupt: operator pause requested: hold", "operator_pause"),
        ("External interrupt: operator abort requested: stop", "operator_abort"),
    ],
)
def test_control_interrupts_receive_structured_stop_kinds(
    fatal_error: str,
    expected: str,
) -> None:
    assert _raw_backend_stop_kind(fatal_error=fatal_error, exit_code=-1) == expected


def test_unknown_wall_clock_interrupt_is_transient() -> None:
    assert _raw_backend_stop_kind(
        fatal_error=(
            "External interrupt: Manager turn wall-clock limit reached after 300s"
        ),
        exit_code=-1,
    ) == "transient_error"


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
                reason="review call denied by the global daily USD cap",
                next_action="resume after the cap resets or is raised",
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


@pytest.mark.parametrize(
    ("stop_kind", "expected_status"),
    [
        ("provider_cooldown", "paused_provider_cooldown"),
        ("operator_abort", "aborted"),
    ],
)
def test_post_edit_review_stop_kind_reaches_mission_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_kind: str,
    expected_status: str,
) -> None:
    persist_vertical(tmp_path, "research")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)

    class EngineerRunsBeforePostEditPasses:
        def run_exec(self, **_kwargs):
            return RunnerResult(exit_code=0, agent_messages=["narrative edit complete"])

    class Reviewer:
        runner = object()

        def evaluate(self, **_kwargs):
            raise AssertionError("integrated review must not run")

    monkeypatch.setattr(
        "argus_skill.reviewer._core._parallel_final_review_passes",
        lambda *_args, **_kwargs: ReviewDecision(
            status="blocked",
            reason="preliminary review stopped",
            next_action="resume",
            backend_unavailable=True,
            backend_stop_kind=stop_kind,  # type: ignore[arg-type]
            input_tokens=6,
            output_tokens=3,
            premium_requests=1.0,
        ),
    )
    engine = SupervisedEngineer(
        engineer_runner=EngineerRunsBeforePostEditPasses(),
        reviewer=Reviewer(),
        engineer_config=EngineerConfig(
            model="test",
            vertical_state_root=tmp_path,
        ),
        reviewer_config=ReviewerConfig(
            model="test",
            active_vertical="research",
            vertical_state_root=str(tmp_path),
        ),
    )
    events: list[dict] = []

    status, rounds, _message, _reason, _thread = engine.run(
        objective="review the paper",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            background_subagent_advisory=False,
            narrative_review_enforcement="blocking",
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == expected_status
    assert rounds[0].round_index == 1
    assert rounds[0].stop_kind == stop_kind
    review_events = [
        event
        for event in events
        if event.get("type") == "round.review.completed"
    ]
    assert len(review_events) == 1
    assert review_events[0]["stop_kind"] == stop_kind
    assert review_events[0]["input_tokens"] == 6
    assert review_events[0]["output_tokens"] == 3
    assert review_events[0]["premium_requests"] == 1.0
