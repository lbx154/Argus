"""Tool-local failures must not become failed Copilot/model turns."""
from __future__ import annotations

import json
import sys

from argus.agent_cli._event_consumers import EventConsumerMixin


def consume(event, state=(None, False, False, None), messages=None):
    return EventConsumerMixin._consume_copilot_event(
        event=event, thread_id=state[0], agent_messages=messages if messages is not None else [],
        turn_completed=state[1], turn_failed=state[2], fatal_error=state[3],
    )


def test_nonzero_terminal_result_after_tool_failure_remains_a_real_failed_call():
    state = consume({"type": "tool.execution_complete", "data": {
        "success": False, "error": {"code": "failure", "message": "Synthetic local tool failure"},
    }})
    state = consume({"type": "result", "exitCode": 2}, state)
    assert state[2] is True
    assert state[3] == "Copilot CLI exited with code 2."


def test_real_local_subprocess_can_recover_from_tool_error_and_complete(tmp_path, monkeypatch):
    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    script = tmp_path / "synthetic_cli.py"
    events = [
        {"type": "tool.execution_complete", "data": {"success": False, "error": {"code": "failure", "message": "Recoverable fixture error"}}},
        {"type": "tool.execution_complete", "data": {"success": True}},
        {"type": "assistant.message", "data": {"content": "Recovered local fixture"}},
        {"type": "result", "exitCode": 0, "sessionId": "recovered-fixture"},
    ]
    script.write_text("import sys\nsys.stdin.read()\n" + "\n".join(
        f"print({json.dumps(event)!r}, flush=True)" for event in events
    ), encoding="utf-8")
    monkeypatch.setattr(AgentCliRunner, "_build_command", lambda *a, **k: [sys.executable, "-u", str(script)])
    runner = AgentCliRunner(agent_bin=sys.executable, backend="copilot")
    result = runner.run_exec(prompt="Synthetic test; no provider", resume_thread_id=None,
                             options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1")
    assert result.exit_code == 0 and result.turn_completed and not result.turn_failed
    assert result.fatal_error is None
    assert result.thread_id == "recovered-fixture"
    assert result.agent_messages[-1] == "Recovered local fixture"
    assert result.tool_activity_observed
