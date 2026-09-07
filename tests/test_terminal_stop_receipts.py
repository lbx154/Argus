"""Continuation context and authoritative operator-stop regression coverage."""
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus_skill.agent_cli._run_exec import _StreamState
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.provider_integrations.authorization_retry import _unauthorized_cause


@pytest.mark.parametrize("reason,kind", [
    ("External interrupt: daemon stop requested", "daemon_shutdown"),
    ("External interrupt: operator pause requested", "operator_pause"),
    ("External interrupt: operator abort requested", "operator_abort"),
])
@pytest.mark.parametrize("old_error", [
    "Reconnecting... 1/5 (stream disconnected: idle timeout waiting for SSE)",
    "HTTP 401 Unauthorized (recovered earlier)",
])
def test_actual_stop_overrides_prior_provider_diagnostic(reason, kind, old_error):
    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    state = _StreamState(thread_id="retained-session", fatal_error=old_error,
                         watchdog_terminated=True, watchdog_reason=reason)
    state.stderr_lines.append(old_error)
    raw = runner._finalize_turn_result(process=SimpleNamespace(returncode=1),
                                      command=["codex"], options=RunnerOptions(), state=state)
    assert raw.fatal_error == reason
    result = translate_result(raw, resume_thread_id=None, copilot_usage=None,
                              usage_accumulator=UsageAccumulator())
    assert result.stop_kind == kind
    assert result.fatal_error == reason
    assert result.stderr_lines == [old_error]
    assert result.thread_id == "retained-session"
    assert _unauthorized_cause(raw) == ""


def test_catalog_warning_cannot_replace_explicit_operator_stop():
    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    reason = "External interrupt: daemon stop requested"
    state = _StreamState(thread_id="retained", watchdog_terminated=True, watchdog_reason=reason)
    state.stderr_lines.append("error: failed to load models: HTTP 401 Unauthorized")
    raw = runner._finalize_turn_result(process=SimpleNamespace(returncode=1),
                                      command=["codex"], options=RunnerOptions(), state=state)
    result = translate_result(raw, resume_thread_id=None, copilot_usage=None,
                              usage_accumulator=UsageAccumulator())
    assert result.fatal_error == reason
    assert result.stop_kind == "daemon_shutdown"
    assert _unauthorized_cause(raw) == ""


def test_actual_provider_failure_without_stop_is_preserved():
    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    state = _StreamState(thread_id="retained", turn_failed=True, fatal_error="HTTP 401 Unauthorized")
    raw = runner._finalize_turn_result(process=SimpleNamespace(returncode=1),
                                      command=["codex"], options=RunnerOptions(), state=state)
    result = translate_result(raw, resume_thread_id=None, copilot_usage=None,
                              usage_accumulator=UsageAccumulator())
    assert result.stop_kind == "permanent_error"
    assert _unauthorized_cause(raw) == "HTTP 401 Unauthorized"


def test_backend_stop_does_not_set_auth_failure_flag(monkeypatch, tmp_path):
    from argus_skill.adapters.agent_cli_backend import AgentCliBackend
    from argus_skill.agent_cli.models import AgentRunResult
    from argus_skill.core.models import RunnerOptions as BackendOptions
    backend=AgentCliBackend(backend="codex")
    raw=AgentRunResult(command=["codex"], exit_code=1, turn_failed=True,
                       fatal_error="External interrupt: daemon stop requested",
                       stderr_lines=["HTTP 401 Unauthorized (recovered earlier)"])
    monkeypatch.setattr(backend._runner, "run_exec", lambda **kwargs: raw)
    result=backend.run_exec(prompt="synthetic fixture", options=BackendOptions(working_dir=str(tmp_path)), run_label="engineer-r1")
    assert result.stop_kind == "daemon_shutdown"
    assert not backend._auth_failure_detected
