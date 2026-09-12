"""Engineer fatal-error classification: a dead Codex/Copilot subprocess is a
transient backend failure, so the engineer retries in a fresh session instead
of burning a full reviewer round on a no-output turn.

Regression for the gpt-5.5-on-fnyweg live run where the engineer's Codex
process intermittently exited 2 ("Process exited with code 2 before turn
completion.") and, because that string was not in the backend-failure pattern
list, every crash wasted a whole reviewer round (mission stalled in research).
"""
from pathlib import Path

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.round_stop_signals import (
    authentication_review_decision,
    backend_failure_cause,
    backend_failure_signature,
    fatal_error_looks_like_auth_failure,
    infrastructure_failure_review_decision,
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


# --------------------------------------------------------------------------- #
# Infrastructure vs model behaviour, read by code from the failure record
# --------------------------------------------------------------------------- #

ECONNREFUSED_RECORD = (
    "Copilot CLI exited with code 1.\n"
    "[relay] forwarding to http://127.0.0.1:18765\n"
    "Error: connect ECONNREFUSED 127.0.0.1:18765\n"
    "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)"
)


@pytest.mark.parametrize(("record", "kind", "line"), [
    (ECONNREFUSED_RECORD, "service_unreachable", "connect ECONNREFUSED 127.0.0.1:18765"),
    ("getaddrinfo ENOTFOUND api.example.test", "service_unreachable",
     "getaddrinfo ENOTFOUND api.example.test"),
    ("Error: getaddrinfo EAI_AGAIN proxy.corp", "service_unreachable",
     "getaddrinfo EAI_AGAIN proxy.corp"),
    ("read ECONNRESET", "service_unreachable", "read ECONNRESET"),
    ("connect ETIMEDOUT 10.0.0.9:443", "service_unreachable", "connect ETIMEDOUT 10.0.0.9:443"),
    ("HTTP 503: Service Unavailable", "service_error", "HTTP 503: Service Unavailable"),
    ("Too Many Requests 429 (retry after 7s)", "service_error",
     "Too Many Requests 429 (retry after 7s)"),
    ("HTTP 421: Misdirected Request", "service_error", "HTTP 421: Misdirected Request"),
    ("bad gateway", "service_error", "bad gateway"),
    ("Error: Failed to load models", "model_catalog", "Failed to load models"),
    ("Error: unable to verify the first certificate", "service_tls",
     "unable to verify the first certificate"),
    ("Error: tunneling socket could not be established, statusCode=407",
     "service_proxy", "tunneling socket could not be established, statusCode=407"),
    ("spawn copilot ENOENT", "cli_missing", "spawn copilot ENOENT"),
    ("/bin/sh: copilot: command not found", "cli_missing", "/bin/sh: copilot: command not found"),
    ("HTTP 401: token expired", "sign_in", "HTTP 401: token expired"),
    ("github-copilot: OAuth refresh failed: timeout", "sign_in",
     "github-copilot: OAuth refresh failed: timeout"),
    ("HTTP 402: Insufficient trial tokens for this request.", "service_quota",
     "HTTP 402: Insufficient trial tokens for this request."),
])
def test_infrastructure_failures_are_named_with_their_cause_line(
    record: str, kind: str, line: str,
) -> None:
    cause = backend_failure_cause(record, exit_code=1)
    assert cause.infrastructure
    assert cause.kind == kind
    assert cause.line == line
    # Every infrastructure failure is also a backend failure for the callers
    # that only ask "did the task itself fail?".
    assert _is_bf(record)


def test_shell_exit_codes_for_a_missing_command_count_without_text() -> None:
    cause = backend_failure_cause("Process exited with code 127 before turn completion.", exit_code=127)
    assert cause.kind == "cli_missing"


@pytest.mark.parametrize("record", [
    # A malformed or refused answer is the model's own doing.
    "turn failed: The model produced a malformed answer",
    "Claude runner reported error_max_turns.",
    # A session that died without a word: a fresh session may well succeed.
    "Process exited with code 2 before turn completion.",
    "Copilot CLI exited with code 1. It printed nothing on stderr; "
    "its own log is under /srv/argus/copilot-home/logs.",
    # Argus's own watchdog ended the call; that says nothing about the service.
    "Forced restart after hard idle timeout (600s without a model stream event).",
    "ACP prompt timed out after 900s",
    # A Codex reconnect notice: the CLI keeps recovering on its own.
    "Reconnecting... 1/100 (stream disconnected before completion)",
    # A path inside a stack frame is not a cause.
    "Process exited with code 1 before turn completion.\n"
    "    at Object.<anonymous> (/home/x/node_modules/proxy-agent/index.js:12:3)",
    "External interrupt: daemon stop requested",
    "",
    None,
])
def test_model_behaviour_and_session_deaths_are_not_infrastructure(record) -> None:
    assert not backend_failure_cause(record).infrastructure


def test_signature_is_keyed_on_the_cause_line_not_the_rest_of_the_tail() -> None:
    first = backend_failure_signature(ECONNREFUSED_RECORD)
    second = backend_failure_signature(
        "Copilot CLI exited with code 1.\n"
        "[relay] retry 3 after 250ms\n"
        "Error: connect ECONNREFUSED 127.0.0.1:18765\n"
        "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1602:16)"
    )
    assert first == second == "connect econnrefused #.#.#.#:#"


def test_infrastructure_review_decision_names_the_cause_and_pauses() -> None:
    cause = backend_failure_cause(ECONNREFUSED_RECORD, exit_code=1)
    decision = infrastructure_failure_review_decision(
        cause=cause, fatal_error=ECONNREFUSED_RECORD, exit_code=1,
    )
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "provider_cooldown"
    assert decision.reason.startswith(
        "The model service could not be reached: connect ECONNREFUSED 127.0.0.1:18765. "
    )
    assert "Technical record: error=Copilot CLI exited with code 1." in decision.reason
    for word in ("backend", "streak", "gate", "handoff", "artifact"):
        assert word not in decision.reason.split("Technical record:")[0].lower()


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

    assert status == "paused_provider_cooldown"
    assert rounds[0].review.backend_stop_kind == "provider_cooldown"
    assert rounds[0].review.operator_question == ""
    review_event = next(
        event for event in events if event["type"] == "round.review.completed"
    )
    assert review_event["operator_question"] == ""
    assert review_event["operator_options"] == []
