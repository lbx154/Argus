"""A CLI that exits without a model turn leaves its reason on stderr.

Last week a hosted run failed for hours while the operator saw only
``Copilot CLI exited with code 1.`` once per round; the actual cause — a relay
process inside the container had died, so the CLI could not reach its local
model port — was written to stderr and nowhere else. The failure record now
carries the CLI's last stderr lines after the runner's own receipt, bounded,
free of terminal colour codes, with credentials redacted and local paths kept.
"""
from __future__ import annotations

import json

from argus_skill.agent_cli import _run_exec
from argus_skill.agent_cli._env import _STDERR_TAIL_CHARS, _STDERR_TAIL_LINES, stderr_tail
from argus_skill.agent_cli.agent_cli_runner import (
    AgentCliRunner,
    RunnerOptions,
    _incomplete_turn_error,
)
from argus_skill.agent_cli.runner_backend import BACKEND_COPILOT
from argus_skill.engineer.round_stop_signals import backend_failure_cause

ECONNREFUSED = "Error: connect ECONNREFUSED 127.0.0.1:18765"


def test_incomplete_turn_keeps_the_explicit_cli_error_in_the_tail() -> None:
    record = _incomplete_turn_error([
        "warning: loading configuration",
        'Error: Model "gpt5.6" from --model flag is not available.',
    ])
    assert record == (
        "warning: loading configuration\n"
        'Error: Model "gpt5.6" from --model flag is not available.'
    )
    # The classifier reads the explicit error line out of the tail.
    assert backend_failure_cause(record).line == (
        'Model "gpt5.6" from --model flag is not available.'
    )


def test_incomplete_turn_without_stderr_is_still_a_failure() -> None:
    assert "without completing" in _incomplete_turn_error([])


def test_receipt_comes_first_and_an_empty_stderr_is_said_so() -> None:
    assert _incomplete_turn_error(
        [ECONNREFUSED], receipt="Copilot CLI exited with code 1.",
    ) == f"Copilot CLI exited with code 1.\n{ECONNREFUSED}"
    assert _incomplete_turn_error(
        [], receipt="Copilot CLI exited with code 1.", log_hint="/srv/argus/copilot-home/logs",
    ) == (
        "Copilot CLI exited with code 1. It printed nothing on stderr; "
        "its own log is under /srv/argus/copilot-home/logs."
    )
    assert _incomplete_turn_error([], receipt="dsh exited with code 2.") == (
        "dsh exited with code 2. It printed nothing on stderr."
    )


def test_tail_is_clean_redacted_and_keeps_local_paths() -> None:
    tail = stderr_tail([
        "\x1b[31mError:\x1b[0m connect ECONNREFUSED 127.0.0.1:18765",
        "Authorization: Bearer xyz0123456789abcdef",
        "    at /home/argus/.argus-skill/copilot-home/relay.js:42:7\x07",
        b"api_key=sk-live-0123456789abcdef".decode(),
        "",
        "",
    ])
    assert tail == (
        "Error: connect ECONNREFUSED 127.0.0.1:18765\n"
        "Authorization: <REDACTED:token>\n"
        "    at /home/argus/.argus-skill/copilot-home/relay.js:42:7\n"
        "api_key= <REDACTED:secret>"
    )
    assert "xyz0123456789abcdef" not in tail
    assert "\x1b" not in tail


def test_tail_is_bounded_by_lines_and_bytes_and_is_valid_utf8() -> None:
    lines = [f"line {index}" for index in range(100)]
    tail = stderr_tail(lines)
    assert tail.splitlines() == lines[-_STDERR_TAIL_LINES:]

    long_lines = [f"line {index} " + "x" * 300 for index in range(40)]
    tail = stderr_tail(long_lines)
    assert len(tail) <= _STDERR_TAIL_CHARS
    # The cut lands on a line boundary so the first kept line is whole.
    assert tail.startswith("line ")

    tail = stderr_tail([b"caf\xc3\xa9 \xff broken".decode("utf-8", errors="replace")])
    tail.encode("utf-8")  # valid UTF-8 or this raises
    assert tail.startswith("café")


class _Stdin:
    def write(self, _text):
        return None

    def close(self):
        return None


class _Process:
    def __init__(self, events, *, stderr, exit_code):
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.stderr = iter(line + "\n" for line in stderr)
        self.stdin = _Stdin()
        self.returncode = exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _run_copilot(monkeypatch, *, stderr, exit_code):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "0")
    events = [{"type": "result", "sessionId": "s-1", "exitCode": exit_code}]
    process = _Process(events, stderr=stderr, exit_code=exit_code)
    monkeypatch.setattr(_run_exec, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        AgentCliRunner, "_resolve_executable", staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **kwargs: ["copilot", "-p"],
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    return runner.run_exec(
        prompt="run the sweep", resume_thread_id=None,
        options=RunnerOptions(), run_label="engineer-r1",
    )


def test_copilot_exit_one_carries_the_stderr_reason_and_reads_as_infrastructure(
    monkeypatch,
) -> None:
    result = _run_copilot(
        monkeypatch,
        stderr=[
            "[relay] forwarding to http://127.0.0.1:18765",
            ECONNREFUSED,
            "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)",
        ],
        exit_code=1,
    )

    assert result.turn_failed is True
    assert result.turn_completed is False
    assert result.thread_id == "s-1"
    assert result.fatal_error is not None
    assert result.fatal_error.startswith("Copilot CLI exited with code 1.\n")
    assert "connect ECONNREFUSED 127.0.0.1:18765" in result.fatal_error

    cause = backend_failure_cause(result.fatal_error, exit_code=result.exit_code)
    assert cause.infrastructure
    assert cause.kind == "service_unreachable"
    assert cause.line == "connect ECONNREFUSED 127.0.0.1:18765"


def test_copilot_exit_one_redacts_a_bearer_token_from_stderr(monkeypatch) -> None:
    result = _run_copilot(
        monkeypatch,
        stderr=["Authorization: Bearer xyz0123456789abcdef", ECONNREFUSED],
        exit_code=1,
    )

    assert result.fatal_error is not None
    assert "xyz0123456789abcdef" not in result.fatal_error
    assert "Authorization: <REDACTED:token>" in result.fatal_error
    assert "connect ECONNREFUSED 127.0.0.1:18765" in result.fatal_error


def test_copilot_exit_one_with_silent_stderr_points_at_its_own_log(monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_HOME", "/srv/argus/copilot-home")
    result = _run_copilot(monkeypatch, stderr=[], exit_code=1)

    assert result.fatal_error == (
        "Copilot CLI exited with code 1. It printed nothing on stderr; "
        "its own log is under /srv/argus/copilot-home/logs."
    )
    # A silent exit says nothing about the service: the session simply died.
    assert not backend_failure_cause(result.fatal_error, exit_code=1).infrastructure
