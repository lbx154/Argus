"""The live-streaming hook in the vendored runner: ``run_exec`` fires
``RunnerOptions.on_agent_message`` with each NEW assistant block the instant it
lands on stdout, so a front-end can stream the reply instead of waiting for the
whole turn. Opt-in — a ``None`` callback leaves the turn byte-for-byte unchanged.

This drives the real ``AgentCliRunner.run_exec`` with a faked copilot CLI process
(no binary, no network): two ``assistant.message`` blocks then a ``result``.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_COPILOT
from argus_skill.agent_cli import agent_cli_runner as runner_mod


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
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
    # Don't require a real copilot binary on PATH.
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda x: x))
    return lines


def test_on_agent_message_fires_per_block_in_order(_fake_copilot, monkeypatch) -> None:
    monkeypatch.setattr(AgentCliRunner, "_build_command",
                        lambda self, **_kw: ["copilot", "-p", "x"])
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


def test_none_callback_leaves_turn_unchanged(_fake_copilot, monkeypatch) -> None:
    """The default (no callback) path must not touch behaviour — the turn still
    collects both blocks and exits cleanly, it just streams nothing."""
    monkeypatch.setattr(AgentCliRunner, "_build_command",
                        lambda self, **_kw: ["copilot", "-p", "x"])
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    result = runner.run_exec(
        prompt="你好", resume_thread_id=None,
        options=RunnerOptions(), run_label="stream-test",
    )
    assert result.agent_messages == ["block one", "block two"]
    assert result.exit_code == 0


def test_callback_exception_never_breaks_the_turn(_fake_copilot, monkeypatch) -> None:
    """A raising UI callback is swallowed — the reply must not be lost to a
    front-end bug."""
    monkeypatch.setattr(AgentCliRunner, "_build_command",
                        lambda self, **_kw: ["copilot", "-p", "x"])
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    def _boom(_block):
        raise RuntimeError("ui exploded")

    result = runner.run_exec(
        prompt="你好", resume_thread_id=None,
        options=RunnerOptions(on_agent_message=_boom), run_label="stream-test",
    )
    assert result.agent_messages == ["block one", "block two"]
    assert result.exit_code == 0
