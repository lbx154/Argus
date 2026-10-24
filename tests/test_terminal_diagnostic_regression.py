"""Current failure semantics must not depend on recovered stderr history."""

from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus.agent_cli.models import AgentRunResult
from argus.core.runner_errors import result_has_pre_provider_refusal
from argus.provider_integrations.authorization_retry import _unauthorized_cause


def test_completed_turn_does_not_resurrect_old_auth():
    raw = AgentRunResult(
        command=["codex"], exit_code=0, turn_completed=True, stderr_lines=["HTTP 401 Unauthorized"]
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
    from argus.provider_integrations.authorization_retry import AuthorizationRetryOwner

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

    monkeypatch.setattr("argus.core.run_gateway.run_exec", run)
    result = owner.run_agent_cli(
        SimpleNamespace(_runner=object()),
        prompt="fixture",
        resume_thread_id=None,
        options=object(),
        run_label="fixture",
    )
    assert len(calls) == requests
    assert result.fatal_error == (terminal if requests == 1 else "HTTP 503")


def _finalize_after_progress(stderr_before, stderr_after):
    from argus.agent_cli._run_exec import _StreamState
    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    state = _StreamState(thread_id="retained", model_progress_observed=True)
    state.stderr_lines.extend([*stderr_before, *stderr_after])
    state.stderr_since_progress.extend(stderr_after)
    return runner._finalize_turn_result(
        process=SimpleNamespace(returncode=1), command=["codex"],
        options=RunnerOptions(), state=state,
    )


def test_stderr_before_progress_stays_history_after_generic_exit():
    raw = _finalize_after_progress(["HTTP 401 Unauthorized (recovered earlier)"], [])
    assert raw.terminal_stderr_lines == []
    assert not _unauthorized_cause(raw)
    assert not result_has_pre_provider_refusal(raw)


_CLAUDE_ASSISTANT_FRAME = (
    '{"type":"assistant","message":{"role":"assistant","content":'
    '[{"type":"text","text":"working on it"}],'
    '"usage":{"input_tokens":1,"output_tokens":1}}}'
)
_COPILOT_DELTA_FRAME = (
    '{"type":"assistant.message","data":{"content":"working on it"}}'
)


def _stream_then_exit(backend, frame, terminal, tmp_path):
    """Drive a real child process: startup noise, a progress frame, the
    turn's closing stderr line, then a nonzero exit."""
    import subprocess
    import sys

    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import sys, time\n"
        "sys.stderr.write('HTTP 503 recovered during startup\\n'); sys.stderr.flush()\n"
        "time.sleep(0.3)\n"
        f"sys.stdout.write({frame!r} + '\\n'); sys.stdout.flush()\n"
        "time.sleep(0.3)\n"
        f"sys.stderr.write({terminal!r} + '\\n'); sys.stderr.flush()\n"
        "sys.exit(1)\n"
    )
    runner = AgentCliRunner(agent_bin=backend, backend=backend)
    process = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
    )
    options = RunnerOptions()
    state = runner._stream_turn_output(
        process=process, command=[backend], options=options,
        run_label=None, thread_id="retained",
    )
    return runner._finalize_turn_result(
        process=process, command=[backend], options=options, state=state
    )


@pytest.mark.parametrize(
    "backend,frame",
    [("claude", _CLAUDE_ASSISTANT_FRAME), ("copilot", _COPILOT_DELTA_FRAME)],
)
@pytest.mark.parametrize(
    "terminal,kind,auth",
    [
        ("ERROR: 429 Too Many Requests: rate limit exceeded", "provider_cooldown", False),
        ("HTTP 401 Unauthorized: token expired", "permanent_error", True),
    ],
)
def test_non_codex_stream_classifies_closing_stderr_after_progress(
    backend, frame, terminal, kind, auth, tmp_path
):
    raw = _stream_then_exit(backend, frame, terminal, tmp_path)
    assert raw.exit_code == 1
    assert raw.model_progress_observed
    assert raw.terminal_stderr_lines == [terminal]
    result = translate_result(
        raw, resume_thread_id=None, copilot_usage=None, usage_accumulator=UsageAccumulator()
    )
    assert result.stop_kind == kind
    assert terminal in result.fatal_error
    assert bool(_unauthorized_cause(raw)) is auth
