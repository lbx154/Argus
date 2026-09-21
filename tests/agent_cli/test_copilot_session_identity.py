"""Durable Copilot session identity for a NEW CLI session (issue #129).

A cold Copilot call used to learn its session id only from the terminal
``result`` event. When the provider-turn watchdog killed the CLI first, the
returned result carried ``thread_id=None`` and the priced usage rows written
under that session were unreachable. The runner now passes the identity Argus
pre-allocated (``--session-id``) and keeps it on every exit path, while any
identity the CLI reports must match the bound one or the call fails closed.

These tests drive the real ``AgentCliRunner.run_exec`` streaming path with a
faked subprocess (no binary, no network, no spend).
"""

from __future__ import annotations

import json

import pytest

from argus.agent_cli import _run_exec as runner_exec
from argus.agent_cli._event_consumers import _CopilotWriteState
from argus.agent_cli._sandbox_commands import _copilot_session_identity_args
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.copilot_session import (
    copilot_cli_supports_session_id,
    reset_copilot_session_id_support_cache,
)
from argus.agent_cli.runner_backend import BACKEND_COPILOT

BOUND = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"
OTHER = "ffffffff-0000-4000-8000-000000000000"


class _FakeStdin:
    def write(self, _s):
        return None

    def close(self):
        return None


class _ExitedFakeProc:
    """A subprocess that already exited while its output drains."""

    def __init__(self, stdout_lines: list[str], *, returncode: int = 0) -> None:
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):  # noqa: ARG002
        return self.returncode


class _LiveFakeProc:
    """A subprocess that streams provider turns, with no ``session.start`` and
    no terminal ``result``, until the runner terminates it (the #129 shape)."""

    def __init__(self) -> None:
        self.stdout = self._endless_turns()
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode: int | None = None
        self.terminated = False

    def _endless_turns(self):
        index = 0
        while not self.terminated:
            yield json.dumps({"type": "model.call_start", "data": {"turnId": str(index)}})
            yield json.dumps({
                "type": "tool.execution_complete",
                "data": {"toolTelemetry": {"properties": {}}},
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
        self.returncode = -9


def _runner(monkeypatch: pytest.MonkeyPatch, process) -> AgentCliRunner:
    monkeypatch.setattr(runner_exec, "spawn_owned_process", lambda *a, **k: process)
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda value: value))
    monkeypatch.setattr(AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot"])

    def _terminate(proc, *, include_detached_children=False):  # noqa: ARG001
        mark = getattr(proc, "mark_terminated", None)
        if callable(mark):
            mark()

    monkeypatch.setattr(AgentCliRunner, "_terminate_process", staticmethod(_terminate))
    return AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)


def _result_line(session_id: str, *, exit_code: int = 0) -> str:
    return json.dumps({"type": "result", "sessionId": session_id, "exitCode": exit_code})


def _session_start_line(session_id: str) -> str:
    return json.dumps({"type": "session.start", "data": {"sessionId": session_id}})


# --------------------------------------------------------------------------- #
# Command construction                                                        #
# --------------------------------------------------------------------------- #


def test_new_session_receives_the_prebound_identity_and_resume_keeps_its_own() -> None:
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    fresh = runner._build_copilot_command(
        resume_thread_id=None, options=RunnerOptions(provider_session_id=BOUND),
    )
    assert fresh[-2:] == ["--session-id", BOUND]
    assert "--resume" not in fresh

    resumed = runner._build_copilot_command(
        resume_thread_id="sess-original", options=RunnerOptions(provider_session_id=None),
    )
    assert resumed[-2:] == ["--resume", "sess-original"]
    assert "--session-id" not in resumed

    unbound = runner._build_copilot_command(resume_thread_id=None, options=RunnerOptions())
    assert "--session-id" not in unbound and "--resume" not in unbound


def test_conflicting_identities_fail_closed_instead_of_one_winning() -> None:
    with pytest.raises(ValueError, match="conflicting Copilot session identity"):
        _copilot_session_identity_args(
            resume_thread_id="sess-original", provider_session_id=BOUND, extra_args=[],
        )
    with pytest.raises(ValueError, match="extra args"):
        _copilot_session_identity_args(
            resume_thread_id=None, provider_session_id=BOUND,
            extra_args=["--session-id", OTHER],
        )
    with pytest.raises(ValueError, match="extra args"):
        _copilot_session_identity_args(
            resume_thread_id="sess-original", provider_session_id=None,
            extra_args=[f"--resume={OTHER}"],
        )
    # The same identity on both sides is not a conflict.
    assert _copilot_session_identity_args(
        resume_thread_id=BOUND, provider_session_id=BOUND, extra_args=[],
    ) == ["--resume", BOUND]


def test_session_id_capability_is_read_from_the_executable_help(monkeypatch) -> None:
    from argus.agent_cli import copilot_session as _copilot_session

    reset_copilot_session_id_support_cache()
    probes: list[str] = []

    def help_text(executable: str) -> str:
        probes.append(executable)
        return "  --session-id <id>\n      Resume ... or set the UUID for a new session"

    monkeypatch.setattr(_copilot_session, "_copilot_help_text", help_text)
    monkeypatch.setattr(_copilot_session.shutil, "which", lambda name: f"/opt/bin/{name}")
    assert copilot_cli_supports_session_id("copilot-new") is True
    assert copilot_cli_supports_session_id("copilot-new") is True
    assert probes == ["/opt/bin/copilot-new"], "the answer is cached per executable"

    monkeypatch.setattr(_copilot_session, "_copilot_help_text", lambda executable: "--resume")
    assert copilot_cli_supports_session_id("copilot-old") is False
    monkeypatch.setattr(_copilot_session.shutil, "which", lambda name: None)
    assert copilot_cli_supports_session_id("copilot-missing") is False
    assert copilot_cli_supports_session_id("") is False
    reset_copilot_session_id_support_cache()


# --------------------------------------------------------------------------- #
# Identity survives every interrupted exit                                    #
# --------------------------------------------------------------------------- #


def test_turn_cap_kill_without_session_start_or_result_keeps_the_bound_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "3")
    process = _LiveFakeProc()
    runner = _runner(monkeypatch, process)

    result = runner.run_exec(
        prompt="long research task", resume_thread_id=None,
        options=RunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
    )

    assert process.terminated is True and result.exit_code == -9
    assert result.provider_turn_cap_hit is True
    assert result.thread_id == BOUND, "the watchdog kill must not lose the session"
    # An identity never implies completion: the work stays failed.
    assert result.turn_completed is False and result.turn_failed is True
    assert str(result.fatal_error).startswith("Provider turn cap reached")
    assert result.session_identity_conflict is None


def test_process_failure_without_terminal_result_keeps_the_bound_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [json.dumps({"type": "assistant.message", "data": {"content": "working..."}})]
    result = _runner(monkeypatch, _ExitedFakeProc(lines, returncode=1)).run_exec(
        prompt="task", resume_thread_id=None,
        options=RunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
    )

    assert result.thread_id == BOUND
    assert result.turn_completed is False and result.turn_failed is True
    assert "exited with code 1" in str(result.fatal_error)


def test_hard_idle_timeout_keeps_the_bound_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SilentProc(_LiveFakeProc):
        def _endless_turns(self):
            import time

            while not self.terminated:
                time.sleep(0.05)
            yield from ()

    process = _SilentProc()
    result = _runner(monkeypatch, process).run_exec(
        prompt="task", resume_thread_id=None,
        options=RunnerOptions(
            provider_session_id=BOUND, watchdog_soft_idle_seconds=0,
            watchdog_stalled_idle_seconds=0, watchdog_hard_idle_seconds=1,
        ),
        run_label="engineer-r1",
    )

    assert process.terminated is True
    assert result.thread_id == BOUND
    assert result.turn_failed is True and result.turn_completed is False
    assert "hard idle timeout" in str(result.fatal_error)


def test_resumed_call_keeps_its_original_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _LiveFakeProc()
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "2")
    result = _runner(monkeypatch, process).run_exec(
        prompt="continue", resume_thread_id="sess-original",
        options=RunnerOptions(), run_label="engineer-r2",
    )
    assert result.thread_id == "sess-original"
    assert result.turn_failed is True


def test_non_copilot_backend_ignores_the_copilot_identity_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AgentCliRunner(agent_bin="codex", backend="codex")
    assert runner._prebound_session_id(RunnerOptions(provider_session_id=BOUND)) is None
    copilot = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    assert copilot._prebound_session_id(RunnerOptions(provider_session_id=BOUND)) == BOUND
    assert copilot._prebound_session_id(RunnerOptions(provider_session_id="  ")) is None


# --------------------------------------------------------------------------- #
# Reported identities must match the bound one                                #
# --------------------------------------------------------------------------- #


def test_matching_terminal_result_completes_and_early_start_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
        _session_start_line(BOUND),
        json.dumps({"type": "assistant.message", "data": {"content": "done"}}),
    ]
    early = _runner(monkeypatch, _ExitedFakeProc(lines)).run_exec(
        prompt="task", resume_thread_id=None,
        options=RunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
    )
    assert early.thread_id == BOUND
    assert early.turn_completed is False and early.turn_failed is True

    complete = _runner(monkeypatch, _ExitedFakeProc([*lines, _result_line(BOUND)])).run_exec(
        prompt="task", resume_thread_id=None,
        options=RunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
    )
    assert complete.thread_id == BOUND
    assert complete.turn_completed is True and complete.turn_failed is False


@pytest.mark.parametrize("mismatch_source", ["session.start", "result"])
def test_mismatched_reported_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch, mismatch_source: str,
) -> None:
    lines = [json.dumps({"type": "assistant.message", "data": {"content": "done"}})]
    if mismatch_source == "session.start":
        lines = [_session_start_line(OTHER), *lines, _result_line(OTHER)]
    else:
        lines = [*lines, _result_line(OTHER)]

    result = _runner(monkeypatch, _ExitedFakeProc(lines)).run_exec(
        prompt="task", resume_thread_id=None,
        options=RunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
    )

    assert result.thread_id is None, "an unrelated session must not be bound"
    assert result.turn_completed is False and result.turn_failed is True
    assert result.session_identity_conflict
    assert OTHER in result.session_identity_conflict and BOUND in result.session_identity_conflict
    assert "session_identity_conflict" in str(result.fatal_error)


def test_resumed_call_rejects_a_result_for_another_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _runner(monkeypatch, _ExitedFakeProc([_result_line(OTHER)])).run_exec(
        prompt="continue", resume_thread_id="sess-original",
        options=RunnerOptions(), run_label="engineer-r2",
    )
    assert result.thread_id is None
    assert result.turn_completed is False and result.turn_failed is True
    assert result.session_identity_conflict


def test_conflict_is_sticky_even_when_a_later_event_repeats_the_other_id() -> None:
    state = _CopilotWriteState()
    thread_id, completed, failed, error = AgentCliRunner._consume_copilot_event(
        event={"type": "session.start", "data": {"sessionId": OTHER}},
        thread_id=BOUND, agent_messages=[], turn_completed=False, turn_failed=False,
        fatal_error=None, write_state=state,
    )
    assert (thread_id, completed, failed) == (None, False, True) and error
    thread_id, completed, failed, _ = AgentCliRunner._consume_copilot_event(
        event={"type": "result", "sessionId": OTHER, "exitCode": 0},
        thread_id=thread_id, agent_messages=[], turn_completed=completed,
        turn_failed=failed, fatal_error=error, write_state=state,
    )
    assert thread_id is None and completed is False and failed is True


def test_unbound_call_still_learns_its_identity_from_the_stream() -> None:
    thread_id, completed, failed, error = AgentCliRunner._consume_copilot_event(
        event={"type": "session.start", "data": {"sessionId": OTHER}},
        thread_id=None, agent_messages=[], turn_completed=False, turn_failed=False,
        fatal_error=None, write_state=_CopilotWriteState(),
    )
    assert (thread_id, completed, failed, error) == (OTHER, False, False, None)
