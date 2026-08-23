"""Engineer fatal-error classification: a dead Codex/Copilot subprocess is a
transient backend failure, so the engineer retries in a fresh session instead
of burning a full reviewer round on a no-output turn.

Regression for the gpt-5.5-on-fnyweg live run where the engineer's Codex
process intermittently exited 2 ("Process exited with code 2 before turn
completion.") and, because that string was not in the backend-failure pattern
list, every crash wasted a whole reviewer round (mission stalled in research).
"""
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.round_stop_signals import (
    authentication_review_decision,
    fatal_error_looks_like_auth_failure,
)
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.engineer.runner import fatal_error_looks_like_backend_failure as _is_bf
from argus_skill.reviewer import ReviewerConfig


def test_codex_subprocess_death_is_backend_failure() -> None:
    # The exact fatal_error agent_cli_runner emits on a nonzero Codex exit.
    assert _is_bf("Process exited with code 2 before turn completion.")
    assert _is_bf("Process exited with code 1 before turn completion.")
    # Copilot CLI variant.
    assert _is_bf("Copilot CLI exited with code 1.")
    # Sanity: the pre-existing transport patterns still match.
    assert _is_bf("Too Many Requests 429")
    assert _is_bf("gateway timeout")


def test_backend_failure_does_not_misclassify() -> None:
    # Recoverable reconnect notices must NOT become a backend-failure state
    # (the CLI keeps recovering), even though they mention a disconnect.
    assert not _is_bf("Reconnecting... 1/100 (stream disconnected before completion)")
    # Normal model prose / check output must never trip this.
    assert not _is_bf("research artifacts are still missing")
    # Intentional daemon shutdown is its own category, not a backend failure.
    assert not _is_bf("External interrupt: daemon stop requested")
    assert not _is_bf(None)
    assert not _is_bf("")


def test_oauth_refresh_failure_requires_operator_authentication() -> None:
    error = "github-copilot: OAuth refresh failed: timeout"

    assert fatal_error_looks_like_auth_failure(error)
    decision = authentication_review_decision(fatal_error=error, exit_code=1)

    assert decision.status == "blocked"
    assert decision.backend_unavailable is True
    assert decision.operator_question is not None
    assert "OAuth refresh failed" in decision.operator_question
    assert "`pi`" in decision.operator_question
    assert "/login" in decision.operator_question
    assert "resume the same mission" in decision.operator_question


class _OAuthFailedRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, **_kwargs):
        self.calls += 1
        return RunnerResult(
            exit_code=1,
            agent_messages=[],
            fatal_error="github-copilot: OAuth refresh failed: timeout",
            stop_kind="permanent_error",
        )


class _UnexpectedReviewer:
    def evaluate(self, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("reviewer must not run while provider login is blocked")


def test_oauth_failure_pauses_once_without_opening_a_new_round(tmp_path: Path) -> None:
    engineer = _OAuthFailedRunner()
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=_UnexpectedReviewer(),
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )

    status, rounds, _final_message, reason, _thread_id = engine.run(
        objective="verify the macOS harness",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "verify it",
        supervised_config=SupervisedConfig(
            max_rounds=10,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0.0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert engineer.calls == 1
    assert len(rounds) == 1
    assert rounds[0].review.status == "blocked"
    assert rounds[0].review.operator_question is not None
    assert "OAuth refresh failed" in rounds[0].review.operator_question
    assert reason == "github-copilot: OAuth refresh failed: timeout"


def test_forbidden_policy_strips_stop_kind_operator_question(
    tmp_path: Path,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    set_active_manager_directive(
        tmp_path,
        "do not ask further questions",
        operator_question_policy="forbid",
    )
    engineer = _OAuthFailedRunner()
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=_UnexpectedReviewer(),
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )

    status, rounds, _final_message, _reason, _thread_id = engine.run(
        objective="verify the harness",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "verify it",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            operator_question_policy_root=tmp_path,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert rounds[0].review.operator_question == ""
    assert rounds[0].review.operator_options == []
    assert rounds[0].review.backend_stop_kind == "permanent_error"
    assert rounds[0].stop_kind == "permanent_error"


def test_forbidden_policy_strips_model_question_event(tmp_path: Path) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    set_active_manager_directive(
        tmp_path,
        "continue without questions",
        operator_question_policy="forbid",
    )

    class _UnavailableModel:
        def run_exec(self, **_kwargs) -> RunnerResult:
            return RunnerResult(
                exit_code=1,
                fatal_error='Error: Model "missing" from --model flag is not available.',
            )

    events: list[dict] = []
    engine = SupervisedEngineer(
        engineer_runner=_UnavailableModel(),
        reviewer=_UnexpectedReviewer(),
        engineer_config=EngineerConfig(model="missing"),
        reviewer_config=ReviewerConfig(model="missing"),
    )
    status, rounds, _message, _reason, _thread = engine.run(
        objective="run the configured model",
        engineer_prompt_builder=lambda _next, _static=True: "run it",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            operator_question_policy_root=tmp_path,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "blocked"
    assert rounds[0].review.backend_stop_kind == "permanent_error"
    assert rounds[0].review.operator_question == ""
    review_event = next(
        event for event in events if event["type"] == "round.review.completed"
    )
    assert review_event["operator_question"] == ""
    assert review_event["operator_options"] == []
