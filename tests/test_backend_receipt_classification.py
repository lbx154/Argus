"""HTTP diagnostics must not turn local history or metrics into provider errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend._result import (
    UsageAccumulator,
    _raw_backend_stop_kind,
    looks_like_auth_failure,
    translate_result,
)
from argus_skill.provider_integrations.authorization_retry import _unauthorized_cause

CAP_RECEIPT = (
    "Provider turn cap reached: this engineer-r1 call used 40 provider turns "
    "(allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP)."
)


@pytest.mark.parametrize("code", [401, 403, 429, 502, 503, 504])
@pytest.mark.parametrize("template", [
    "2026-09-07T04:28:00.{code}010Z WARN local history projection failed",
    "2026-09-07T04:28:00.{code}Z WARN local history projection failed",
    "expected ordinal {code}, got 382",
    "mse=0.{code}",
    "processed {code} records",
])
def test_non_http_numbers_do_not_classify_provider_failures(code, template):
    diagnostic = template.format(code=code)
    assert _raw_backend_stop_kind(fatal_error=diagnostic, exit_code=1) == "backend_unavailable"
    assert not looks_like_auth_failure([diagnostic])
    assert not _unauthorized_cause(SimpleNamespace(exit_code=1, fatal_error=diagnostic))


@pytest.mark.parametrize(("code", "kind"), [
    (401, "permanent_error"), (403, "permanent_error"),
    (429, "provider_cooldown"), (502, "transient_error"),
    (503, "transient_error"), (504, "transient_error"),
])
@pytest.mark.parametrize("template", [
    "request failed: HTTP {code}",
    "HTTP/1.1 {code}",
    "unexpected status code: {code}",
    "HTTP status code: {code}",
    '{{"status_code": {code}}}',
])
def test_contextual_http_status_preserves_failure_classification(code, kind, template):
    diagnostic = template.format(code=code)
    assert _raw_backend_stop_kind(fatal_error=diagnostic, exit_code=1) == kind
    assert looks_like_auth_failure([diagnostic]) is (code in {401, 403})
    assert bool(_unauthorized_cause(SimpleNamespace(
        exit_code=1, fatal_error=diagnostic,
    ))) is (code == 401)


@pytest.mark.parametrize("history", [
    "2026-09-07T04:28:00.401010Z WARN local history projection failed",
    "Reconnecting... 1/3 (HTTP 401 Unauthorized)",
    "HTTP 429 Too Many Requests",
    "ERROR: Failed to load models: HTTP 503 Service Unavailable",
    "access denied by policy settings",
])
def test_authoritative_cap_receipt_preserves_usage_and_historical_diagnostics(history):
    raw = SimpleNamespace(
        exit_code=1, fatal_error=CAP_RECEIPT, turn_failed=True,
        stderr_lines=[history], stdout_lines=[], thread_id="capped-session",
        agent_messages=["completed the first four experiments"],
        json_events=[{"type": "turn.completed", "usage": {
            "input_tokens": 1000, "output_tokens": 100,
        }}],
    )
    result = translate_result(
        raw, resume_thread_id=None, copilot_usage=None,
        usage_accumulator=UsageAccumulator(),
    )
    assert result.fatal_error == CAP_RECEIPT
    assert result.stop_kind == "backend_unavailable"
    assert result.stderr_lines == [history]
    assert result.thread_id == "capped-session"
    assert result.input_tokens == 1000
    assert result.output_tokens == 100
    assert result.agent_messages == raw.agent_messages
    assert not _unauthorized_cause(raw)
    combined = CAP_RECEIPT + "\n" + history
    assert _raw_backend_stop_kind(fatal_error=combined, exit_code=1) == "backend_unavailable"
    assert not looks_like_auth_failure([combined])


@pytest.mark.parametrize("diagnostic", [
    "401 Missing bearer", "401 Unauthorized", "HTTP error 401: rejected",
    'request failed: {"status":401}',
])
def test_genuine_unauthorized_receipts_still_require_replay(diagnostic):
    raw = SimpleNamespace(exit_code=1, fatal_error=diagnostic)
    assert _unauthorized_cause(raw) == diagnostic
    assert looks_like_auth_failure([diagnostic])


@pytest.mark.parametrize(("diagnostic", "kind"), [
    ("403 Forbidden", "permanent_error"),
    ("429 Too Many Requests", "provider_cooldown"),
    ("502 Bad Gateway", "transient_error"),
    ("503 Service Unavailable", "transient_error"),
    ("504 Gateway Timeout", "transient_error"),
])
def test_status_reason_without_http_prefix_remains_a_provider_error(diagnostic, kind):
    assert _raw_backend_stop_kind(fatal_error=diagnostic, exit_code=1) == kind


def test_cap_mention_in_stderr_does_not_hide_a_real_terminal_auth_failure():
    raw = SimpleNamespace(exit_code=1, fatal_error="HTTP 401", stderr_lines=[CAP_RECEIPT])
    assert _unauthorized_cause(raw) == "HTTP 401"
    assert looks_like_auth_failure([CAP_RECEIPT, "HTTP 401"])
    assert _raw_backend_stop_kind(
        fatal_error="HTTP 401\n" + CAP_RECEIPT, exit_code=1,
    ) == "permanent_error"


def test_successful_turn_ignores_recovered_auth_failure_in_stderr():
    raw = SimpleNamespace(
        exit_code=0, fatal_error=None, turn_failed=False,
        stderr_lines=["HTTP 401 Unauthorized"], stdout_lines=[], thread_id="recovered",
        agent_messages=["done"], json_events=[],
    )
    result = translate_result(
        raw, resume_thread_id=None, copilot_usage=None,
        usage_accumulator=UsageAccumulator(),
    )
    assert result.stop_kind is None
    assert result.fatal_error is None
    assert not _unauthorized_cause(raw)


def test_generic_failed_turn_still_uses_catalog_startup_stderr():
    history = "ERROR: Failed to load models: HTTP 503 Service Unavailable"
    raw = SimpleNamespace(
        exit_code=1, fatal_error="process exited", turn_failed=True,
        stderr_lines=[history], stdout_lines=[], thread_id=None,
        agent_messages=[], json_events=[],
    )
    result = translate_result(
        raw, resume_thread_id=None, copilot_usage=None,
        usage_accumulator=UsageAccumulator(),
    )
    assert result.fatal_error == history
    assert result.stop_kind == "transient_error"
