"""The per-call provider-turn allowance (``ARGUS_SKILL_PROVIDER_TURN_CAP``).

One CLI call is one conversation with the provider, and the CLI resends the
whole grown transcript on every provider turn inside it — a measured worst case
was 430 provider turns and 59.9M input tokens in ONE Engineer call. None of the
driven CLIs accepts a per-call turn limit on its command line, so the runner
counts provider turns from the event stream and winds the call down at the
allowance; the round loop then continues the task in a fresh session.

These tests drive the real ``AgentCliRunner.run_exec`` streaming path with a
faked subprocess (no binary, no network, no spend).
"""

from __future__ import annotations

import json
import os
import time

import pytest

from argus_skill.agent_cli import agent_cli_runner as runner_mod
from argus_skill.agent_cli._env import _provider_turn_cap
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
)


class _FakeStdin:
    def write(self, _s):
        return None

    def close(self):
        return None


class _ExitedFakeProc:
    """A subprocess that already exited cleanly while its output drains.

    Same shape as the established fake in ``test_run_exec_stream_callback.py``:
    ``poll()`` returning 0 is safe because the stream loop only breaks once both
    pipe sentinels have drained.
    """

    def __init__(self, stdout_lines: list[str]) -> None:
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return 0

    def wait(self, timeout=None):  # noqa: ARG002
        return 0


class _LiveFakeProc:
    """A subprocess that keeps streaming provider turns until terminated.

    ``poll()`` returns ``None`` while alive — the shape of a real long-running
    CLI — so the allowance stop exercises the same terminate path production
    takes. The endless stdout stream ends when the runner terminates the
    process, letting the reader thread close its pipe.
    """

    def __init__(self) -> None:
        self.stdout = self._endless_turns()
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode: int | None = None
        self.terminated = False

    def _endless_turns(self):
        index = 0
        while not self.terminated:
            yield json.dumps({
                "type": "model.call_start",
                "data": {"turnId": str(index)},
            })
            yield json.dumps({
                "type": "model.call_finished",
                "data": {"turnId": str(index), "outcome": "success"},
            })
            index += 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):  # noqa: ARG002
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def mark_terminated(self) -> None:
        self.terminated = True
        self.returncode = -15


def _copilot_turn_lines(turns: int, *, with_result: bool = False) -> list[str]:
    lines: list[str] = []
    for index in range(turns):
        lines.append(json.dumps({
            "type": "model.call_start",
            "data": {"turnId": str(index)},
        }))
        lines.append(json.dumps({
            "type": "model.call_finished",
            "data": {"turnId": str(index), "outcome": "success"},
        }))
    if with_result:
        lines.append(json.dumps({
            "type": "assistant.message",
            "data": {"content": "all done"},
        }))
        lines.append(json.dumps({
            "type": "result",
            "sessionId": "sess-under-cap",
            "exitCode": 0,
        }))
    return lines


def _capped_runner(
    monkeypatch: pytest.MonkeyPatch,
    process,
    *,
    backend: str = BACKEND_COPILOT,
) -> tuple[AgentCliRunner, list[str]]:
    terminations: list[str] = []
    monkeypatch.setattr(
        runner_mod.subprocess, "Popen", lambda *args, **kwargs: process
    )
    monkeypatch.setattr(
        AgentCliRunner, "_resolve_executable", staticmethod(lambda value: value)
    )
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **_kw: [backend, "-p"]
    )

    def _terminate(proc, *, include_detached_children=False):  # noqa: ARG001
        terminations.append("terminate")
        mark = getattr(proc, "mark_terminated", None)
        if callable(mark):
            mark()

    monkeypatch.setattr(
        AgentCliRunner, "_terminate_process", staticmethod(_terminate)
    )
    return AgentCliRunner(agent_bin=backend, backend=backend), terminations


def test_engineer_call_winds_down_at_the_provider_turn_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "3")
    process = _LiveFakeProc()
    runner, terminations = _capped_runner(monkeypatch, process)

    result = runner.run_exec(
        prompt="long research task",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert terminations == ["terminate"]
    assert process.terminated is True
    assert result.turn_failed is True
    assert result.provider_turn_cap_hit is True
    assert result.provider_turns == 3
    assert str(result.fatal_error or "").startswith("Provider turn cap reached")
    assert "ARGUS_SKILL_PROVIDER_TURN_CAP" in str(result.fatal_error)


def test_call_under_the_allowance_completes_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "5")
    process = _ExitedFakeProc(_copilot_turn_lines(2, with_result=True))
    runner, terminations = _capped_runner(monkeypatch, process)

    result = runner.run_exec(
        prompt="short task",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert terminations == []
    assert process.terminated is False
    assert result.turn_completed is True
    assert result.provider_turn_cap_hit is False
    assert result.provider_turns == 2
    assert result.thread_id == "sess-under-cap"


def test_zero_disables_the_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "0")
    runner, terminations = _capped_runner(
        monkeypatch, _ExitedFakeProc(_copilot_turn_lines(4, with_result=True))
    )

    result = runner.run_exec(
        prompt="task",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert terminations == []
    assert result.turn_completed is True
    assert result.provider_turns == 0  # counting is off entirely


def test_manager_labels_are_never_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "2")
    runner, terminations = _capped_runner(
        monkeypatch, _ExitedFakeProc(_copilot_turn_lines(5, with_result=True))
    )

    result = runner.run_exec(
        prompt="what does the backlog hold?",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="manager-ask",
    )

    assert terminations == []
    assert result.turn_completed is True


def test_allowance_applies_to_reviewer_labels() -> None:
    assert _provider_turn_cap("reviewer") == 40
    assert _provider_turn_cap("reviewer-cold-read") == 40
    assert _provider_turn_cap("engineer-r7") == 40
    assert _provider_turn_cap("planner.cycle0") == 0
    assert _provider_turn_cap("manager-ask") == 0
    assert _provider_turn_cap(None) == 0


def test_wind_down_call_gets_only_a_small_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PROVIDER_TURN_CAP", raising=False)
    assert _provider_turn_cap("engineer-r3.winddown") == 8
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "4")
    assert _provider_turn_cap("engineer-r3.winddown") == 4


@pytest.mark.skipif(os.name == "nt", reason="POSIX stub script")
def test_real_subprocess_smoke_stub_cli_is_wound_down_at_the_allowance(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end through the real spawn/stream/terminate path.

    A stub CLI prints far more provider-turn receipts than the allowance and
    then sleeps as a long-running provider would; the runner must terminate it
    at the allowance and return the wind-down receipt. No real backend, no
    network, no spend.
    """
    stub = tmp_path / "stub-copilot"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "cat > /dev/null <&0 &\n"
        "for i in $(seq 1 50); do\n"
        "  echo '{\"type\":\"model.call_finished\",\"data\":{\"turnId\":\"'$i'\"}}'\n"
        "done\n"
        "sleep 120\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "5")

    runner = AgentCliRunner(agent_bin=str(stub), backend=BACKEND_COPILOT)
    started = time.monotonic()
    result = runner.run_exec(
        prompt="smoke",
        resume_thread_id=None,
        options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )

    assert result.provider_turn_cap_hit is True
    assert result.provider_turns == 5
    assert result.turn_failed is True
    assert str(result.fatal_error or "").startswith("Provider turn cap reached")
    # The 120s sleep never ran out: the runner ended the call itself.
    assert time.monotonic() - started < 60


@pytest.mark.parametrize(
    ("backend", "event", "ends_turn"),
    [
        (BACKEND_COPILOT, {"type": "model.call_finished", "data": {}}, True),
        (BACKEND_COPILOT, {"type": "assistant.message_delta", "data": {}}, False),
        (BACKEND_CLAUDE, {"type": "assistant", "message": {}}, True),
        (BACKEND_CLAUDE, {"type": "result", "subtype": "success"}, False),
        (
            BACKEND_CODEX,
            {"type": "item.completed", "item": {"type": "command_execution"}},
            True,
        ),
        (
            BACKEND_CODEX,
            {"type": "item.completed", "item": {"type": "reasoning"}},
            False,
        ),
        (BACKEND_OPENCODE, {"type": "step_finish", "part": {"reason": "tool-calls"}}, True),
        (BACKEND_OPENCODE, {"type": "text", "part": {"text": "hi"}}, False),
        (
            BACKEND_PI,
            {"type": "message_end", "message": {"role": "assistant"}},
            True,
        ),
        (
            BACKEND_PI,
            {"type": "message_end", "message": {"role": "user"}},
            False,
        ),
    ],
)
def test_provider_turn_boundaries_per_dialect(backend, event, ends_turn) -> None:
    runner = AgentCliRunner.__new__(AgentCliRunner)
    runner.backend = backend
    assert runner._event_ends_provider_turn(event) is ends_turn
