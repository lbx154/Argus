"""HTTP diagnostics must not turn local history or metrics into provider errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend._result import (
    UsageAccumulator,
    _raw_backend_stop_kind,
    looks_like_auth_failure,
    translate_result,
)
from argus.provider_integrations.authorization_retry import _unauthorized_cause


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
