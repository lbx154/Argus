"""The live-streaming hook in the vendored runner: ``run_exec`` fires
``RunnerOptions.on_agent_message`` with each NEW assistant block the instant it
lands on stdout, so a front-end can stream the reply instead of waiting for the
whole turn. Opt-in — a ``None`` callback leaves the turn byte-for-byte unchanged.

This drives the real ``AgentCliRunner.run_exec`` with a faked copilot CLI process
(no binary, no network): two ``assistant.message`` blocks then a ``result``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from argus_skill.agent_cli import agent_cli_runner as runner_mod
from argus_skill.agent_cli.agent_cli_runner import (
    AgentCliRunner,
    RunnerOptions,
    _turn_wall_clock_seconds,
)
from argus_skill.agent_cli.runner_backend import BACKEND_CLAUDE, BACKEND_COPILOT


class _FakeStdin:
    def write(self, _s):  # copilot path only closes stdin
        return None

    def close(self):
        return None


class _FakeProc:
    """Minimal stand-in for a copilot subprocess: yields preset stdout lines,
    empty stderr, and reports a clean exit. ``poll()`` returning 0 is safe — the
    run_exec loop only breaks once BOTH pipe sentinels have drained."""

    def __init__(self, stdout_lines: list[str]) -> None:
        self.stdout = iter(stdout_lines)  # 'for line in pipe' consumes an iterator
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):  # noqa: ARG002
        self.returncode = 0
        return 0


@pytest.fixture()
def _fake_copilot(monkeypatch: pytest.MonkeyPatch):
    lines = [
        json.dumps({"type": "assistant.message", "data": {"content": "block one"}}),
        json.dumps({"type": "assistant.message", "data": {"content": "block two"}}),
        json.dumps({"type": "result", "sessionId": "sess-1", "exitCode": 0}),
    ]
    popen_kwargs: dict[str, object] = {}

    def _popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return _FakeProc(lines)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _popen)
    # Don't require a real copilot binary on PATH.
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda x: x))
    return popen_kwargs


def test_on_agent_message_fires_per_block_in_order(_fake_copilot, monkeypatch) -> None:
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot", "-p", "x"]
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    got: list[str] = []
    result = runner.run_exec(
        prompt="你好",
        resume_thread_id=None,
        options=RunnerOptions(on_agent_message=got.append),
        run_label="stream-test",
    )
    # Every block streamed live, in arrival order …
    assert got == ["block one", "block two"]
    # … and the authoritative result still holds the full message list + thread.
    assert result.agent_messages == ["block one", "block two"]
    assert result.last_agent_message == "block two"
    assert result.thread_id == "sess-1"
    assert result.exit_code == 0
    assert _fake_copilot["encoding"] == "utf-8"
    assert _fake_copilot["errors"] == "replace"


def test_none_callback_leaves_turn_unchanged(_fake_copilot, monkeypatch) -> None:
    """The default (no callback) path must not touch behaviour — the turn still
    collects both blocks and exits cleanly, it just streams nothing."""
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot", "-p", "x"]
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    result = runner.run_exec(
        prompt="你好",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="stream-test",
    )
    assert result.agent_messages == ["block one", "block two"]
    assert result.exit_code == 0


@pytest.mark.parametrize("exit_code", [0, 1])
def test_copilot_model_response_waits_for_authoritative_result(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    final = "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer"
    lines = [
        json.dumps({"type": "assistant.message", "data": {"content": final}}),
        json.dumps(
            {
                "type": "model.response",
                "data": {
                    "kind": "response",
                    "response": {
                        "content": final,
                        "responses_message_status": "completed",
                        "phase": "final_answer",
                    },
                },
            }
        ),
        json.dumps({"type": "result", "sessionId": "sess-final", "exitCode": exit_code}),
    ]
    process = _FakeProc(lines)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p"],
    )

    def terminate(proc, *, include_detached_children=False):  # noqa: ARG001
        raise AssertionError("model responses must not terminate the provider")

    monkeypatch.setattr(AgentCliRunner, "_terminate_process", staticmethod(terminate))

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="finish",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert result.thread_id == "sess-final"
    assert result.turn_completed is (exit_code == 0)
    assert result.turn_failed is (exit_code != 0)
    assert result.agent_messages == [final]


def test_claude_stream_records_tool_activity_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "session_id": "claude-session",
                "message": {
                    "model": "glm-5.2",
                    "content": [
                        {"type": "text", "text": "Inspecting the source."},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                            "input": {"file_path": "src/app.py"},
                        },
                    ],
                    "usage": {"input_tokens": 120, "output_tokens": 15},
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "claude-session",
                "result": "done",
            }
        ),
    ]
    monkeypatch.setattr(
        runner_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["claude", "-p"],
    )

    result = AgentCliRunner(
        agent_bin="claude",
        backend=BACKEND_CLAUDE,
    ).run_exec(
        prompt="fix it",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="planner.cycle0",
    )

    assert result.turn_completed is True
    assert result.tool_activity_observed is True
    assert result.usage_model == "glm-5.2"
    assert result.agent_messages == ["Inspecting the source.", "done"]


def test_tool_activity_detector_covers_codex_items_and_plain_text() -> None:
    assert AgentCliRunner._event_has_tool_activity(
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "pytest -q"},
        }
    )
    assert not AgentCliRunner._event_has_tool_activity(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "finished"},
        }
    )
    for event_type in (
        "mcp.tools.list_changed",
        "session.mcp_servers_loaded",
        "session.mcp_server_status_changed",
        "session.tools_updated",
        "session.skills_loaded",
    ):
        assert not AgentCliRunner._event_has_tool_activity({"type": event_type})


def test_clean_exit_with_message_but_no_terminal_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intermediate Copilot output must not masquerade as a completed turn.

    A long tool-using Engineer can emit useful messages and still exit before
    Copilot's terminal ``result`` event. Without that event there is no
    sessionId or authoritative completion receipt, so the round must be retried
    as a fresh backend failure rather than recorded with ``thread_id=null``.
    """
    lines = [
        json.dumps(
            {
                "type": "assistant.message",
                "data": {"content": "partial work summary"},
            }
        ),
        json.dumps(
            {
                "type": "assistant.tool_call_delta",
                "data": {
                    "toolCallId": "call-1",
                    "toolName": "bash",
                    "inputDelta": "{",
                },
            }
        ),
    ]
    monkeypatch.setattr(
        runner_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p", "x"],
    )

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="bounded mission",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert result.exit_code == 0
    assert result.agent_messages == ["partial work summary"]
    assert result.thread_id is None
    assert result.turn_completed is False
    assert result.turn_failed is True
    assert result.fatal_error == "Agent CLI exited without completing a model turn."


def test_cli_process_starts_in_its_own_posix_session(_fake_copilot, monkeypatch) -> None:
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **_kw: ["copilot", "-p", "x"],
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    runner.run_exec(
        prompt="x",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="stream-test",
    )
    if runner_mod.os.name == "nt":
        assert _fake_copilot["creationflags"] & runner_mod.subprocess.CREATE_NO_WINDOW
        startup = _fake_copilot["startupinfo"]
        assert startup is not None
        assert startup.dwFlags & runner_mod.subprocess.STARTF_USESHOWWINDOW
        assert startup.wShowWindow == runner_mod.subprocess.SW_HIDE
    else:
        assert _fake_copilot["start_new_session"] is True


def test_callback_exception_never_breaks_the_turn(_fake_copilot, monkeypatch) -> None:
    """A raising UI callback is swallowed — the reply must not be lost to a
    front-end bug."""
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot", "-p", "x"]
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    def _boom(_block):
        raise RuntimeError("ui exploded")

    result = runner.run_exec(
        prompt="你好",
        resume_thread_id=None,
        options=RunnerOptions(on_agent_message=_boom),
        run_label="stream-test",
    )
    assert result.agent_messages == ["block one", "block two"]
    assert result.exit_code == 0


def test_runner_retains_bounded_stream_tail_with_exact_counts(monkeypatch) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "m1", "deltaContent": str(index)},
            }
        )
        for index in range(20)
    ]
    lines.extend(
        [
            json.dumps(
                {
                    "type": "assistant.message",
                    "data": {"messageId": "m1", "content": "final"},
                }
            ),
            json.dumps({"type": "result", "sessionId": "sess-1", "exitCode": 0}),
        ]
    )

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_CAPTURE_STDOUT_LINES", "3")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_CAPTURE_JSON_EVENTS", "4")
    monkeypatch.setattr(
        runner_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p", "x"],
    )

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="bounded",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="stream-test",
    )

    assert result.stdout_line_count == len(lines)
    assert result.json_event_count == len(lines)
    assert len(result.stdout_lines) == 3
    assert [event["type"] for event in result.json_events] == [
        "assistant.message",
        "result",
    ]
    assert result.last_agent_message == "final"
    assert result.thread_id == "sess-1"


def test_post_exit_drain_keeps_queued_terminal_event_with_slow_callback(
    monkeypatch,
) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant.message",
                "data": {"content": f"block-{index}"},
            }
        )
        for index in range(75)
    ]
    lines.append(
        json.dumps(
            {
                "type": "result",
                "sessionId": "sess-slow",
                "exitCode": 0,
            }
        )
    )
    monkeypatch.setattr(
        runner_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p", "x"],
    )

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="bounded",
        resume_thread_id=None,
        options=RunnerOptions(on_agent_message=lambda _message: time.sleep(0.02)),
        run_label="stream-test",
    )

    assert result.turn_completed is True
    assert result.turn_failed is False
    assert result.thread_id == "sess-slow"


def test_runner_keeps_usage_bearing_delta_for_accounting() -> None:
    assert (
        AgentCliRunner._retain_json_event(
            {
                "type": "assistant.message_delta",
                "data": {"deltaContent": "x", "inputTokens": 17},
            }
        )
        is True
    )
    assert (
        AgentCliRunner._retain_json_event(
            {
                "type": "assistant.message_delta",
                "data": {"deltaContent": "x"},
            }
        )
        is False
    )


def test_engineer_turn_wall_clock_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", raising=False)
    assert _turn_wall_clock_seconds("engineer-r1") == 0
    assert _turn_wall_clock_seconds("reviewer") == 0
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", "90")
    assert _turn_wall_clock_seconds("engineer-r7") == 90
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", "0")
    assert _turn_wall_clock_seconds("engineer-r7") == 0


@pytest.mark.parametrize(
    "run_label",
    [
        "manager-classify-grounded",
        "manager.skill_placement",
        "router-classify",
        "simple-1",
        "chat-1",
        "self-debug",
        "self-implement",
        "self-micro",
        "self-review",
        "self-synthesize",
    ],
)
def test_manager_turn_wall_clock_is_unbounded_by_default(monkeypatch, run_label: str) -> None:
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_TURN_MAX_SECONDS", raising=False)

    assert _turn_wall_clock_seconds(run_label) == 0

    monkeypatch.setenv("ARGUS_SKILL_MANAGER_TURN_MAX_SECONDS", "45")
    assert _turn_wall_clock_seconds(run_label) == 45
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_TURN_MAX_SECONDS", "0")
    assert _turn_wall_clock_seconds(run_label) == 0


@pytest.mark.parametrize("run_label", ["planner-bounded-plan", "planner-preview"])
def test_planner_turn_wall_clock_is_unbounded(monkeypatch, run_label: str) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_TURN_MAX_SECONDS", "1")
    assert _turn_wall_clock_seconds(run_label) == 0


def test_scientist_skill_distill_wall_clock_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SCIENTIST_TURN_MAX_SECONDS", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", "0")

    assert _turn_wall_clock_seconds("scientist.skill_distill") == 0

    monkeypatch.setenv("ARGUS_SKILL_SCIENTIST_TURN_MAX_SECONDS", "45")
    assert _turn_wall_clock_seconds("scientist.skill_distill") == 45
    monkeypatch.setenv("ARGUS_SKILL_SCIENTIST_TURN_MAX_SECONDS", "0")
    assert _turn_wall_clock_seconds("scientist.skill_distill") == 0


CHATTER = """
def chatter(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        print('ERROR live_writer expected ordinal 23, got 22', file=sys.stderr, flush=True)
        time.sleep(0.02)
"""


def run_stream(body: str, options: RunnerOptions):
    events: list[tuple[str, str]] = []
    runner = AgentCliRunner(
        agent_bin=sys.executable,
        backend="codex",
        event_callback=lambda stream, line: events.append((stream, line)),
    )
    command = [sys.executable, "-u", "-c", "import sys, time, json\n" + CHATTER + body]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        state = runner._stream_turn_output(
            process=process,
            command=command,
            options=options,
            run_label="engineer-r1",
            thread_id="stored-thread",
        )
        return state, events
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_stderr_chatter_does_not_hide_warning_or_stalled_stage():
    state, events = run_stream(
        "chatter(2.6)\n",
        RunnerOptions(
            watchdog_soft_idle_seconds=1,
            watchdog_stalled_idle_seconds=2,
            watchdog_hard_idle_seconds=0,
        ),
    )
    warnings = [line for _, line in events if line.startswith("[watchdog]")]
    assert sum("No model stream event" in line for line in warnings) == 1
    assert sum("likely stalled" in line for line in warnings) == 1
    assert not state.watchdog_terminated  # Default warning-only policy is preserved.
    assert state.stderr_line_count > 20
    assert any("expected ordinal 23, got 22" in line for line in state.stderr_lines)


def test_explicit_hard_idle_is_not_bypassed_by_stderr_chatter():
    state, events = run_stream(
        "chatter(2.6)\n",
        RunnerOptions(watchdog_hard_idle_seconds=1),
    )
    assert state.watchdog_terminated
    assert "hard idle timeout" in str(state.watchdog_reason).lower()
    assert sum("Forced restart" in line for _, line in events) == 1


def test_stdout_tool_progress_keeps_the_stream_active():
    event = {"type": "item.updated", "item": {"id": "command-1", "type": "command_execution", "status": "in_progress", "aggregated_output": "tick"}}
    body = "for _ in range(13):\n    print(" + repr(json.dumps(event)) + ", flush=True)\n    chatter(0.2)\n"
    state, events = run_stream(
        body,
        RunnerOptions(watchdog_soft_idle_seconds=1, watchdog_hard_idle_seconds=1),
    )
    assert not state.watchdog_terminated
    assert state.stdout_line_count == 13
    assert not any(line.startswith("[watchdog]") for _, line in events)


def test_real_stdout_event_resets_warning_stage_after_stderr_chatter():
    state, events = run_stream(
        "chatter(1.3)\nprint(json.dumps({'type': 'turn.started'}), flush=True)\nchatter(1.3)\n",
        RunnerOptions(watchdog_soft_idle_seconds=1, watchdog_stalled_idle_seconds=2),
    )
    assert not state.watchdog_terminated
    assert sum("No model stream event" in line for _, line in events) == 2
    assert not any("likely stalled" in line for _, line in events)


def test_inactivity_callback_is_reached_during_stderr_chatter():
    snapshots = []

    def on_idle(snapshot):
        snapshots.append(snapshot)
        return "restart"

    state, _ = run_stream(
        "chatter(2.6)\n",
        RunnerOptions(watchdog_soft_idle_seconds=1, inactivity_callback=on_idle),
    )
    assert state.watchdog_terminated
    assert len(snapshots) == 1
    assert snapshots[0].idle_seconds >= 1
    assert snapshots[0].stderr_tail
    assert "stall sub-agent" in str(state.watchdog_reason)
