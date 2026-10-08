"""A call never reports a session the provider did not create, and a resume of
a session that no longer exists continues once in a fresh session.

The early-exit path already drops a pre-bound id the CLI never made durable.
The same rule holds when the call ends in an exception (login required, a
runner crash): with no durable event seen and no session in the store, the
pre-bound id names nothing, and resuming it later fails with "No session ...
matched". Synthetic identities and faked runner boundaries: no real CLI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli import _run_exec as runner_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as CliRunnerOptions
from argus.agent_cli.runner_backend import BACKEND_COPILOT
from argus.core.models import RunnerOptions, RunnerResult
from argus.core.run_gateway import run_exec as gateway_run_exec
from argus.provider_integrations.authorization_retry import BackendLoginRequired

BOUND = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"


@pytest.fixture
def copilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.copilot_cli_supports_session_id",
        lambda executable: True,
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: object(),
    )
    store: dict[str, Any] = {"sessions": set()}

    def read_usage(cursor, *, session_id):  # noqa: ARG001
        return object() if session_id in store["sessions"] else None

    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since", read_usage,
    )
    backend = AgentCliBackend(backend="copilot")

    def install(raise_exc):
        def run_exec(self: Any, **kwargs: Any):
            store["options"] = kwargs["options"]
            raise raise_exc(kwargs["options"].provider_session_id)

        monkeypatch.setattr(backend._runner.__class__, "run_exec", run_exec, raising=True)

    store["install"] = install
    store["backend"] = backend
    return store


def _call(copilot, tmp_path: Path, *, resume: str | None = None):
    return copilot["backend"].run_exec(
        prompt="task", options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="engineer-r1", resume_thread_id=resume,
    )


def test_runner_crash_before_any_durable_event_reports_no_session(tmp_path, copilot) -> None:
    def crash(_bound):
        exc = RuntimeError("reader failed")
        exc.provider_session_observed = False
        return exc

    copilot["install"](crash)
    result = _call(copilot, tmp_path)

    assert result.thread_id is None
    assert result.fatal_error.startswith("RuntimeError:")


def test_runner_crash_without_evidence_reports_no_session(tmp_path, copilot) -> None:
    copilot["install"](lambda _bound: RuntimeError("reader failed"))

    assert _call(copilot, tmp_path).thread_id is None


def test_runner_crash_after_a_durable_event_keeps_the_session(tmp_path, copilot) -> None:
    def crash(_bound):
        exc = RuntimeError("reader failed")
        exc.provider_session_observed = True
        return exc

    copilot["install"](crash)
    result = _call(copilot, tmp_path)

    assert result.thread_id == copilot["options"].provider_session_id


def test_runner_crash_keeps_a_session_the_store_holds(tmp_path, copilot) -> None:
    def crash(bound):
        copilot["sessions"].add(bound)
        return RuntimeError("reader failed")

    copilot["install"](crash)
    result = _call(copilot, tmp_path)

    assert result.thread_id == copilot["options"].provider_session_id


def test_login_required_reports_no_new_session(tmp_path, copilot) -> None:
    copilot["install"](lambda _bound: BackendLoginRequired("401 unauthorized"))

    with pytest.raises(BackendLoginRequired):
        _call(copilot, tmp_path)
    usage = json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[-1])
    assert usage["thread_id"] is None


def test_crash_on_a_resumed_call_keeps_its_own_session(tmp_path, copilot) -> None:
    copilot["install"](lambda _bound: RuntimeError("reader failed"))

    assert _call(copilot, tmp_path, resume="sess-original").thread_id == "sess-original"


# --------------------------------------------------------------------------- #
# the runner hands the durable-event evidence to its caller                   #
# --------------------------------------------------------------------------- #


class _Stdin:
    def write(self, _s):
        return None

    def close(self):
        return None


class _Proc:
    def __init__(self, stdout: list[str]) -> None:
        self.stdout = iter(stdout)
        self.stderr = iter([])
        self.stdin = _Stdin()
        self.returncode = 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):  # noqa: ARG002
        return self.returncode


@pytest.mark.parametrize(("lines", "observed"), [
    ([json.dumps({"type": "session.mcp_server_status_changed", "ephemeral": True})], False),
    ([json.dumps({"type": "user.message", "data": {"content": "x"}})], True),
])
def test_runner_exception_carries_durable_event_evidence(monkeypatch, lines, observed) -> None:
    monkeypatch.setattr(runner_exec, "spawn_owned_process", lambda *a, **k: _Proc(lines))
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda v: v))
    monkeypatch.setattr(AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot"])
    original = AgentCliRunner._stream_turn_output

    def stream_then_fail(self, **kwargs):
        original(self, **kwargs)
        raise RuntimeError("reader failed")

    monkeypatch.setattr(AgentCliRunner, "_stream_turn_output", stream_then_fail)
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    with pytest.raises(RuntimeError) as caught:
        runner.run_exec(
            prompt="task", resume_thread_id=None,
            options=CliRunnerOptions(provider_session_id=BOUND), run_label="engineer-r1",
        )

    assert caught.value.provider_session_observed is observed


# --------------------------------------------------------------------------- #
# a resume of a missing session continues once in a fresh session             #
# --------------------------------------------------------------------------- #

_MISSING = "Error: No session, task, or name matched 'stale-thread'."


class _Backend:
    def __init__(self, *, fresh_fails: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fresh_fails = fresh_fails

    def run_exec(self, **kwargs: Any) -> RunnerResult:
        self.calls.append(kwargs)
        if kwargs.get("resume_thread_id") or self.fresh_fails:
            return RunnerResult(exit_code=1, thread_id=kwargs.get("resume_thread_id"),
                                fatal_error=_MISSING)
        return RunnerResult(exit_code=0, thread_id="fresh-thread")


def test_missing_session_resume_continues_in_a_fresh_session() -> None:
    backend = _Backend()

    result = gateway_run_exec(
        backend, prompt="continue the task", run_label="planner",
        resume_thread_id="stale-thread",
    )

    assert [call["resume_thread_id"] for call in backend.calls] == ["stale-thread", None]
    assert backend.calls[1]["prompt"].endswith("continue the task")
    assert "fresh session" in backend.calls[1]["prompt"]
    assert result.exit_code == 0 and result.thread_id == "fresh-thread"


def test_fresh_fallback_happens_once_and_never_rebinds_the_stale_session() -> None:
    backend = _Backend(fresh_fails=True)

    result = gateway_run_exec(
        backend, prompt="continue", run_label="planner", resume_thread_id="stale-thread",
    )

    assert len(backend.calls) == 2
    assert result.exit_code == 1 and result.thread_id is None


def test_caller_with_its_own_handoff_can_keep_the_failure() -> None:
    backend = _Backend()

    result = gateway_run_exec(
        backend, prompt="continue", run_label="manager", resume_thread_id="stale-thread",
        fresh_on_missing_resume=False,
    )

    assert len(backend.calls) == 1 and result.exit_code == 1
