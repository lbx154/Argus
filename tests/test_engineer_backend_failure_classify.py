"""Engineer fatal-error classification: a dead Codex/Copilot subprocess is a
transient backend failure, so the engineer retries in a fresh session instead
of burning a full reviewer round on a no-output turn.

Regression for the gpt-5.5-on-fnyweg live run where the engineer's Codex
process intermittently exited 2 ("Process exited with code 2 before turn
completion.") and, because that string was not in the backend-failure pattern
list, every crash wasted a whole reviewer round (mission stalled in research).
"""

import pytest

from argus.engineer.round_stop_signals import (
    authentication_review_decision,
    backend_failure_cause,
    fatal_error_looks_like_auth_failure,
)
from argus.engineer.runner import fatal_error_looks_like_backend_failure as _is_bf


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


def test_oauth_refresh_failure_requires_operator_authentication() -> None:
    error = "github-copilot: OAuth refresh failed: timeout"

    assert fatal_error_looks_like_auth_failure(error)
    decision = authentication_review_decision(fatal_error=error, exit_code=1)

    assert decision.status == "operator"
    assert decision.backend_unavailable is True
    assert decision.operator_question is not None
    assert "OAuth refresh failed" in decision.operator_question
    assert "`pi`" in decision.operator_question
    assert "/login" in decision.operator_question
    assert "resume the same mission" in decision.operator_question
