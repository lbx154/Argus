"""Selected upstream protocol fixes without weakening the local no-replay policy."""
from __future__ import annotations

import sys
from types import SimpleNamespace

from argus_skill.adapters.agent_cli_backend._result import _raw_backend_stop_kind
from argus_skill.agent_cli._event_consumers import EventConsumerMixin


def consume(event, *, messages=None):
    return EventConsumerMixin._consume_copilot_event(
        event=event, thread_id="parent", agent_messages=messages if messages is not None else [],
        turn_completed=False, turn_failed=False, fatal_error=None,
    )


def test_new_copilot_delegate_events_cannot_finish_or_replace_the_parent():
    messages = []
    for event in [
        {"type": "assistant.message", "agentId": "child", "data": {"content": "child text"}},
        {"type": "result", "agentId": "child", "exitCode": 0, "sessionId": "child-session"},
        {"type": "session.error", "agentId": "child", "data": {"message": "delegated operation failed"}},
    ]:
        assert consume(event, messages=messages) == ("parent", False, False, None)
    assert messages == []
    state = consume({"agentId": "child", "error": {"code": "provider_stream_failed", "message": "fatal provider envelope"}})
    assert state[2] and "fatal provider envelope" in state[3]


def test_copilot_query_retains_http_status_and_unsupported_requests_are_permanent():
    state = consume({"type": "session.error", "data": {
        "errorType": "query", "statusCode": 400, "message": "unsupported trial request",
    }})
    assert state[2] and "HTTP 400" in state[3]
    assert _raw_backend_stop_kind(fatal_error=state[3], exit_code=1) == "permanent_error"
    assert _raw_backend_stop_kind(fatal_error="HTTP 503 unavailable", exit_code=1) == "transient_error"
    assert _raw_backend_stop_kind(fatal_error="HTTP 429 rate limited", exit_code=1) == "provider_cooldown"


def test_new_tool_metadata_keeps_the_local_error_scope():
    state = consume({"type": "tool.execution_complete", "data": {
        "success": False, "error": {"message": "tool-local error"},
        "toolTelemetry": {"properties": {"read_target_state": "active"}},
    }})
    assert state[1:] == (False, False, None)


def test_actual_nonzero_cli_exit_preserves_the_stderr_cause(tmp_path, monkeypatch):
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TRIAL", "0")
    script = tmp_path / "failed_cli.py"
    script.write_text("import sys\nsys.stdin.read()\nprint('Error: synthetic runtime missing', file=sys.stderr, flush=True)\nsys.exit(7)\n", encoding="utf-8")
    monkeypatch.setattr(AgentCliRunner, "_acp_enabled", lambda *a, **k: False)
    monkeypatch.setattr(AgentCliRunner, "_build_command", lambda *a, **k: [sys.executable, "-u", str(script)])
    runner = AgentCliRunner(agent_bin=sys.executable, backend="copilot")
    result = runner.run_exec(prompt="Local fixture, never a provider", resume_thread_id=None,
                             options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1")
    assert result.exit_code == 7 and result.turn_failed
    assert "code 7" in result.fatal_error and "synthetic runtime missing" in result.fatal_error


def test_provider_fence_requires_explicit_recovery_even_in_continuous_mode(tmp_path, monkeypatch):
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    memory = LifeMemory.open(tmp_path / "life")
    item = BacklogItem.new(title="held", objective="synthetic task")
    item.status = "paused_provider_fence"
    memory.backlog.add(item)
    calls = []

    class Runner:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(success=True, status="done", stop_kind=None, rounds=1)

    supervisor = LifeSupervisor(memory=memory, runner=Runner(),
                                sink=SimpleNamespace(handle_event=lambda event: None), config=LifeSupervisorConfig(
        continuous=True, continuous_objective="Do not bypass the fence", budget=LifeBudget(max_missions=1),
        poll_interval_seconds=0.0,
    ))
    result = supervisor.run()
    assert result["stopped_by"] == "paused_provider_fence"
    assert not calls
    assert memory.backlog.all()[0].status == "paused_provider_fence"
    memory.backlog.resume_paused(item.id)
    result = supervisor.run()
    assert result["missions_run"] == 1 and len(calls) == 1
