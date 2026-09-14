"""Qoder backend reuses the Claude Code code path.

``qodercli`` is a Claude Code fork: same headless argv, same stream-json event
schema. Argus therefore routes ``qoder`` through ``_build_claude_command`` and
``_consume_claude_event`` (via ``CLAUDE_FAMILY``). These tests pin that reuse so
a future divergence in either path is caught.
"""
from __future__ import annotations

from pathlib import Path

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import (
    BACKEND_QODER,
    CLAUDE_FAMILY,
    default_runner_bin,
    normalize_runner_backend,
)


def _runner(agent_bin: str = "qodercli") -> AgentCliRunner:
    return AgentCliRunner(agent_bin=agent_bin, backend=BACKEND_QODER)


def test_qoder_is_registered_and_in_claude_family() -> None:
    assert normalize_runner_backend("qoder") == BACKEND_QODER
    assert normalize_runner_backend("QODER") == BACKEND_QODER
    assert default_runner_bin(BACKEND_QODER) == "qodercli"
    assert BACKEND_QODER in CLAUDE_FAMILY


def test_qoder_command_matches_claude_headless_shape_without_verbose() -> None:
    # qodercli emits the same stream-json as claude but REJECTS --verbose
    # (`unknown option '--verbose'`), so qoder's argv drops that one flag.
    command = _runner()._build_command(
        resume_thread_id="sess-1",
        options=RunnerOptions(model="qoder-max", dangerous_yolo=True),
    )

    assert Path(command[0]).name == "qodercli"
    assert "--verbose" not in command
    assert command[1:] == [
        "-p",
        "--output-format",
        "stream-json",
        "--model",
        "qoder-max",
        "--permission-mode",
        "bypass_permissions",
        "--resume",
        "sess-1",
    ]


def test_qoder_flag_dialect_differs_from_claude() -> None:
    # qodercli's three argv divergences from claude: no --verbose,
    # --reasoning-effort (not --effort), snake_case permission modes.
    from argus.agent_cli.runner_backend import BACKEND_CLAUDE

    def build(backend: str, **opts) -> list[str]:
        return AgentCliRunner(agent_bin="x", backend=backend)._build_command(
            resume_thread_id=None, options=RunnerOptions(**opts)
        )

    qoder = build("qoder", model="m", reasoning_effort="xhigh", full_auto=True)
    assert "--verbose" not in qoder
    assert "--reasoning-effort" in qoder and "--effort" not in qoder
    assert qoder[qoder.index("--reasoning-effort") + 1] == "xhigh"
    assert "accept_edits" in qoder and "acceptEdits" not in qoder

    claude = build(
        BACKEND_CLAUDE,
        model="m",
        reasoning_effort="xhigh",
        dangerous_yolo=True,
    )
    assert "--verbose" in claude
    assert "--effort" in claude and "--reasoning-effort" not in claude
    assert claude[claude.index("--effort") + 1] == "xhigh"
    assert "bypassPermissions" in claude


def test_qoder_read_only_restricts_tools() -> None:
    command = _runner()._build_command(
        resume_thread_id=None,
        options=RunnerOptions(sandbox_mode="read-only", full_auto=True),
    )

    assert command.count("--tools") == 1
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
    # read-only wins over full-auto: no acceptEdits/bypass permission flag.
    assert "--permission-mode" not in command


def test_qoder_stream_tracks_session_text_and_completion() -> None:
    runner = _runner()
    messages: list[str] = []
    state = runner._consume_event(
        event={
            "type": "assistant",
            "session_id": "qsid",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
            },
        },
        thread_id=None,
        agent_messages=messages,
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )
    state = runner._consume_event(
        event={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "qsid",
            "result": "done",
        },
        thread_id=state[0],
        agent_messages=messages,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )

    assert messages == ["done"]
    assert state == ("qsid", True, False, None)


def test_qoder_error_result_fails_closed() -> None:
    state = _runner()._consume_event(
        event={
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "session_id": "qsid",
            "result": "authentication failed",
        },
        thread_id=None,
        agent_messages=[],
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state[0] == "qsid"
    assert state[1] is False
    assert state[2] is True
    assert state[3]
