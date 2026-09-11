from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus_skill.agent_cli._event_consumers import EventConsumerMixin
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


def test_copilot_trial_quota_error_pauses_after_one_attempt(tmp_path: Path) -> None:
    message = "402 Insufficient trial tokens for this request."
    state = (None, False, False, None)
    for event in [
        {"type": "session.error", "data": {
            "errorType": "query", "message": message, "statusCode": 402,
        }},
        {"type": "result", "sessionId": "trial-session", "exitCode": 1},
    ]:
        state = EventConsumerMixin._consume_copilot_event(
            event=event, thread_id=state[0], agent_messages=[],
            turn_completed=state[1], turn_failed=state[2], fatal_error=state[3],
        )
    assert state == ("trial-session", False, True, f"HTTP 402: {message}")
    stop_kind = _raw_backend_stop_kind(fatal_error=state[3], exit_code=1)
    assert stop_kind == "provider_fence"
    status, backend, events = _run_engineer(tmp_path, stop_kind)
    assert status == "paused_provider_fence"
    assert backend.calls == 1
    assert not any(event["type"] == "round.backend_failure.backoff" for event in events)


def test_native_delegate_query_error_does_not_fail_parent_turn() -> None:
    state = EventConsumerMixin._consume_copilot_event(
        event={"type": "session.error", "agentId": "delegate", "data": {
            "errorType": "query", "message": "402 Insufficient trial tokens for this request.",
            "statusCode": 402,
        }},
        thread_id="parent", agent_messages=[], turn_completed=False,
        turn_failed=False, fatal_error=None,
    )
    assert state == ("parent", False, False, None)


@pytest.mark.parametrize("message", [
    "402 Insufficient trial tokens for this request.",
    '402 {"error":{"code":"trial_quota_exceeded","message":"Insufficient trial tokens for this request."}}',
    "HTTP 402: Payment Required",
])
def test_trial_quota_receipts_are_provider_fences(message: str) -> None:
    assert _raw_backend_stop_kind(fatal_error=message, exit_code=1) == "provider_fence"


def test_rejected_trial_request_does_not_retry_as_a_backend_outage(tmp_path: Path) -> None:
    message = (
        "HTTP 400: 400 Unsupported trial request; use text/tool "
        "Chat Completions with model argus-trial."
    )
    stop_kind = _raw_backend_stop_kind(fatal_error=message, exit_code=1)

    assert stop_kind == "permanent_error"
    status, backend, events = _run_engineer(tmp_path, stop_kind)
    assert status == "error"
    assert backend.calls == 1
    assert not any(event["type"] == "round.backend_failure.backoff" for event in events)
    assert _raw_backend_stop_kind(
        fatal_error="HTTP 400: context length exceeded", exit_code=1,
    ) == "backend_unavailable"


def test_backend_unavailable_holds_identical_failures_until_the_round_budget(
    tmp_path: Path,
) -> None:
    status, backend, _events = _run_engineer(tmp_path, "backend_unavailable")

    # A run of IDENTICAL backend failures is one continuing cause: instead of
    # failing fast at the threshold (which fed a paid replanning cycle for the
    # same failure), the round loop holds and retries until the round budget
    # (max_rounds=3 here) ends the mission.
    assert status == "error"
    assert backend.calls == 3


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


def test_stop_kinds_have_reader_facing_clauses_in_both_languages() -> None:
    from argus_skill.core.stop_kinds import (
        STOP_KINDS,
        pause_status_clause,
        stop_kind_clause,
    )

    for kind in STOP_KINDS:
        english = stop_kind_clause(kind)
        chinese = stop_kind_clause(kind, chinese=True)
        assert english and chinese
        for text in (english, chinese):
            assert "_" not in text
            for word in ("backend", "daemon", "provider_", "stop_kind"):
                assert word not in text.lower()
    assert stop_kind_clause("not-a-kind") == ""
    assert stop_kind_clause(None) == ""
    assert stop_kind_clause("daemon_shutdown") == "Argus was stopped"
    assert stop_kind_clause("daemon_shutdown", chinese=True) == "Argus 被停止"

    assert pause_status_clause("paused_daemon_shutdown") == "Argus was stopped"
    assert pause_status_clause("paused_budget") == "the project reached its budget limit"
    assert pause_status_clause("paused_external_work", chinese=True) == (
        "需要先等 Argus 之外的工作完成"
    )
    assert pause_status_clause("paused_something_new") == "the work was paused"
    assert pause_status_clause("failed") == ""
