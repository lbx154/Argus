"""No provider execution: exercise the actual spawn/stream interface."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

from argus.agent_cli import _copilot_session, _run_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core.provider_sessions import bind_before_dispatch, read_bindings
from tests.agent_cli.test_provider_turn_cap import _ExitedFakeProc, _LiveFakeProc


def setup_runner(monkeypatch, tmp_path, process, call_id="fixture-call"):
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")
    monkeypatch.setattr(_copilot_session, "supports_session_id", lambda _: True)
    monkeypatch.setattr(runner, "_resolve_executable", lambda value: value)
    monkeypatch.setattr(runner, "_acp_enabled", lambda *_: False)
    def spawn(command, **kwargs):
        decision = next(d for d in read_bindings(tmp_path)["decisions"] if d["call_id"] == call_id)
        assert decision["session_id"] in command
        return process
    monkeypatch.setattr(_run_exec, "spawn_owned_process", spawn)
    monkeypatch.setattr(runner, "_terminate_process", lambda proc, **_: proc.mark_terminated())
    def bind(identity, resumed):
        bind_before_dispatch(tmp_path, call_id=call_id, session_id=identity, resumed=resumed)
    return runner, RunnerOptions(_bind_provider_session=bind)


def test_cap_before_result_keeps_durable_uuid(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "3")
    runner, options = setup_runner(monkeypatch, tmp_path, _LiveFakeProc())
    result = runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert str(UUID(result.thread_id)) == result.thread_id
    assert result.thread_id == read_bindings(tmp_path)["decisions"][0]["session_id"]
    assert result.provider_turn_cap_hit and result.turn_failed and not result.turn_completed


@pytest.mark.parametrize("events", [[], [{"type": "session.start", "data": {"sessionId": "resume-fixture"}}]])
def test_resume_and_early_start_never_imply_completion(monkeypatch, tmp_path, events):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([json.dumps(e) for e in events]))
    result = runner.run_exec(prompt="fixture", resume_thread_id="resume-fixture", options=options)
    assert result.thread_id == "resume-fixture" and not result.turn_completed
    assert "--session-id" not in result.command
    assert "--resume" in result.command


@pytest.mark.parametrize("event", [
    {"type": "session.start", "data": {"sessionId": "unrelated"}},
    {"type": "result", "sessionId": "unrelated", "exitCode": 0},
])
def test_identity_mismatch_cannot_complete(monkeypatch, tmp_path, event):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([json.dumps(event)]))
    result = runner.run_exec(prompt="fixture", resume_thread_id="original", options=options)
    assert result.thread_id == "original"
    assert not result.turn_completed and result.turn_failed
    assert result.fatal_error == "provider_session_identity_conflict"


@pytest.mark.parametrize("flag", ["--session-id=evil", "--resume", "--continue"])
def test_conflicting_flags_refused_before_spawn(monkeypatch, tmp_path, flag):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([]))
    options.extra_args = [flag]
    with pytest.raises(ValueError, match="identity flags"):
        runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert read_bindings(tmp_path)["decisions"] == []


def test_unsupported_cli_fails_closed_without_dispatch(monkeypatch, tmp_path):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([]))
    monkeypatch.setattr(_copilot_session, "supports_session_id", lambda _: False)
    with pytest.raises(RuntimeError, match="compatibility"):
        runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert not read_bindings(tmp_path)["decisions"]


def test_startup_exception_retains_persisted_binding(monkeypatch, tmp_path):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([]))
    def fail(*args, **kwargs):
        assert read_bindings(tmp_path)["decisions"]
        raise OSError("synthetic spawn failure")
    monkeypatch.setattr(_run_exec, "spawn_owned_process", fail)
    with pytest.raises(OSError):
        runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert len(read_bindings(tmp_path)["decisions"]) == 1


def test_concurrent_bindings_are_unique_and_durable(tmp_path):
    import uuid
    def bind(i):
        identity = str(uuid.uuid4())
        bind_before_dispatch(tmp_path, call_id=f"call-{i}", session_id=identity, resumed=False)
        return identity
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(bind, range(8)))
    assert len(set(identities)) == 8
    assert {d["session_id"] for d in read_bindings(tmp_path)["decisions"]} == set(identities)


def test_durable_write_failure_prevents_spawn(monkeypatch, tmp_path):
    from argus.core import provider_sessions
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([]))
    def fail(*args):
        raise OSError("synthetic audit write failure")
    monkeypatch.setattr(provider_sessions, "write_bindings", fail)
    with pytest.raises(OSError):
        runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert not read_bindings(tmp_path)["decisions"]


@pytest.mark.parametrize("text,expected", [("  --session-id <id>  Session UUID\n", True),
                                           ("  --session-identity <id>\n", False)])
def test_help_probe_uses_only_documented_flag(monkeypatch, text, expected):
    from types import SimpleNamespace
    def probe(command, **kwargs):
        assert command == ["fixture-copilot", "--help"] and kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stdout=text)
    monkeypatch.setattr(_copilot_session.subprocess, "run", probe)
    assert _copilot_session.supports_session_id("fixture-copilot") is expected


def test_backend_exception_retains_identity_and_attempts_usage_lookup(monkeypatch, tmp_path):
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.core.models import RunnerOptions as BackendOptions
    from argus.provider_integrations import copilot_usage

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    backend = AgentCliBackend(backend="copilot")
    project = tmp_path / "project"
    backend.set_usage_context(project_root=project, mission_id="fixture-mission")
    observed = []
    monkeypatch.setattr(copilot_usage, "read_copilot_usage_since",
                        lambda cursor, *, session_id: observed.append(session_id))
    def fail(_runner, **kwargs):
        kwargs["options"]._bind_provider_session("fixture-bound", False)
        assert read_bindings(project)["decisions"][0]["session_id"] == "fixture-bound"
        raise RuntimeError("synthetic post-spawn failure")
    monkeypatch.setattr(AgentCliRunner, "run_exec", fail)
    result = backend.run_exec(prompt="fixture", run_label="engineer-r1",
                              options=BackendOptions(model="gpt-5.6-sol", working_dir=str(tmp_path)))
    assert result.thread_id == "fixture-bound" and result.exit_code != 0
    assert observed == ["fixture-bound"]
    assert result.stop_kind == "backend_unavailable"
    rows = [json.loads(line) for line in (project / "usage.jsonl").read_text().splitlines()]
    assert rows[0]["thread_id"] == "fixture-bound" and rows[0]["status"] == "error"


def test_external_cancellation_keeps_binding(monkeypatch, tmp_path):
    runner, options = setup_runner(monkeypatch, tmp_path, _LiveFakeProc())
    options.external_interrupt_reason_provider = lambda: (
        "synthetic cancellation" if read_bindings(tmp_path)["decisions"] else None)
    result = runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="engineer-r1")
    assert result.thread_id == read_bindings(tmp_path)["decisions"][0]["session_id"]
    assert not result.turn_completed and result.turn_failed


def test_wall_clock_timeout_retains_binding(monkeypatch, tmp_path):
    runner, options = setup_runner(monkeypatch, tmp_path, _LiveFakeProc())
    monkeypatch.setattr(_run_exec, "_turn_wall_clock_seconds", lambda _: 0.001)
    result = runner.run_exec(prompt="fixture", resume_thread_id=None, options=options, run_label="fixture")
    assert result.thread_id == read_bindings(tmp_path)["decisions"][0]["session_id"]
    assert not result.turn_completed and result.turn_failed
