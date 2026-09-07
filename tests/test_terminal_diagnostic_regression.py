"""Current failure semantics must not depend on recovered stderr history."""

from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.models import RunnerOptions
from argus_skill.core.runner_errors import result_has_pre_provider_refusal
from argus_skill.provider_integrations.authorization_retry import _unauthorized_cause


@pytest.mark.parametrize(
    "terminal,kind",
    [
        ("HTTP 503 Service Unavailable", "transient_error"),
        ("stream disconnected before completion: idle timeout waiting for SSE", "transient_error"),
        ("HTTP 429 Too Many Requests", "provider_cooldown"),
        ("External interrupt: daemon stop requested", "daemon_shutdown"),
        ("Provider turn cap reached: allowance 40", "backend_unavailable"),
    ],
)
@pytest.mark.parametrize(
    "history",
    [
        "HTTP 401 Unauthorized (recovered earlier)",
        "error: failed to load models: HTTP 401 Unauthorized (recovered earlier)",
    ],
)
def test_terminal_failure_is_shared_by_translation_auth_and_accounting(
    monkeypatch, tmp_path, terminal, kind, history
):
    backend = AgentCliBackend(backend="codex")
    raw = AgentRunResult(
        command=["codex"],
        exit_code=1,
        turn_failed=True,
        fatal_error=terminal,
        stderr_lines=[history],
    )
    monkeypatch.setattr(backend._runner, "run_exec", lambda **kwargs: raw)
    result = backend.run_exec(
        prompt="fixture", options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1"
    )
    assert result.stop_kind == kind
    assert result.fatal_error == terminal
    assert result.stderr_lines == [history]
    assert not backend._auth_failure_detected
    assert not _unauthorized_cause(raw)
    assert not result_has_pre_provider_refusal(raw)


@pytest.mark.parametrize("fatal", [None, "Process exited with code 1 before turn completion."])
@pytest.mark.parametrize(
    "diagnostic,kind,auth",
    [
        ("HTTP 401 Unauthorized", "permanent_error", True),
        ("HTTP 503 Service Unavailable", "transient_error", False),
        ("error: failed to load models: HTTP 401 Unauthorized", "permanent_error", True),
    ],
)
def test_startup_failure_uses_latest_diagnostic(fatal, diagnostic, kind, auth):
    raw = AgentRunResult(
        command=["codex"],
        exit_code=1,
        turn_failed=True,
        fatal_error=fatal,
        stderr_lines=["HTTP 401 recovered", diagnostic],
    )
    result = translate_result(
        raw, resume_thread_id=None, copilot_usage=None, usage_accumulator=UsageAccumulator()
    )
    assert result.stop_kind == kind
    assert bool(_unauthorized_cause(raw)) is auth


def test_completed_turn_does_not_resurrect_old_auth():
    raw = AgentRunResult(
        command=["codex"], exit_code=0, turn_completed=True, stderr_lines=["HTTP 401 Unauthorized"]
    )
    assert not _unauthorized_cause(raw)
    assert not result_has_pre_provider_refusal(raw)


def test_missing_terminal_message_does_not_keep_recovered_event():
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner

    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    _, _, failed, error = runner._consume_codex_event(
        event={"type": "turn.failed", "error": {}},
        thread_id=None,
        agent_messages=[],
        turn_completed=False,
        turn_failed=False,
        fatal_error="HTTP 401 Unauthorized (recovered earlier)",
    )
    assert failed
    assert "401" not in str(error)


def test_generic_exit_after_progress_cannot_be_startup_auth():
    raw = SimpleNamespace(
        exit_code=1,
        turn_failed=True,
        fatal_error="Process exited with code 1 before turn completion.",
        stderr_lines=["error: failed to load models: HTTP 401 recovered"],
        tool_activity_observed=True,
        json_events=[],
        agent_messages=[],
    )
    assert not _unauthorized_cause(raw)
    assert not result_has_pre_provider_refusal(raw)


@pytest.mark.parametrize(
    "terminal,requests",
    [
        ("HTTP 503", 1),
        ("HTTP 429", 1),
        ("idle timeout waiting for SSE", 1),
        ("HTTP 401 Unauthorized", 2),
    ],
)
def test_relay_owner_replays_only_current_401(monkeypatch, tmp_path, terminal, requests):
    from argus_skill.provider_integrations.authorization_retry import AuthorizationRetryOwner

    owner = AuthorizationRetryOwner()
    calls = []
    credential = tmp_path / "dummy-env"
    credential.write_text("FIXTURE_TOKEN=placeholder\n")
    monkeypatch.setattr(
        owner,
        "_credential_snapshot",
        lambda *args: SimpleNamespace(
            key=("fixture",), path=credential, env_key="FIXTURE_TOKEN", rejected="placeholder"
        ),
    )

    def run(*args, **kwargs):
        calls.append(True)
        return AgentRunResult(
            command=["fixture"],
            exit_code=1,
            turn_failed=True,
            fatal_error=terminal if len(calls) == 1 else "HTTP 503",
            stderr_lines=["HTTP 401 Unauthorized (recovered earlier)"],
        )

    monkeypatch.setattr("argus_skill.core.run_gateway.run_exec", run)
    result = owner.run_agent_cli(
        SimpleNamespace(_runner=object()),
        prompt="fixture",
        resume_thread_id=None,
        options=object(),
        run_label="fixture",
    )
    assert len(calls) == requests
    assert result.fatal_error == (terminal if requests == 1 else "HTTP 503")


def test_progress_survives_bounded_event_capture():
    raw = AgentRunResult(
        command=["fixture"],
        exit_code=1,
        turn_failed=True,
        fatal_error="Process exited with code 1 before turn completion.",
        model_progress_observed=True,
        json_events=[],
        stderr_lines=["HTTP 401 recovered"],
    )
    assert not _unauthorized_cause(raw)


def test_new_provider_error_replaces_recoverable_error_and_progress_clears_it():
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner

    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    error = "HTTP 401 Unauthorized"
    for event, expected in [
        ({"type": "error", "message": "HTTP 503"}, "HTTP 503"),
        ({"type": "item.completed", "item": {"type": "command_execution"}}, None),
    ]:
        _, _, _, error = runner._consume_codex_event(
            event=event,
            thread_id=None,
            agent_messages=[],
            turn_completed=False,
            turn_failed=False,
            fatal_error=error,
        )
        assert error == expected


def test_empty_terminal_error_replaces_recovered_401():
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner

    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    _, _, failed, error = runner._consume_codex_event(
        event={"type": "turn.failed", "error": {}}, thread_id=None,
        agent_messages=[], turn_completed=False, turn_failed=False,
        fatal_error="HTTP 401 recovered",
    )
    assert failed
    assert error == "Backend reported a failed turn."
