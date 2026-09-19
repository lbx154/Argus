"""Offline regressions for the September 18 PR130 review."""
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend._budget_monitor import LiveBudgetMonitor
from argus.agent_cli import _copilot_session, _run_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core.cost_control import cost_control_snapshot, reserve_call_budget
from argus.core.provider_sessions import read_bindings
from tests.agent_cli.test_copilot_durable_session import setup_runner
from tests.agent_cli.test_provider_turn_cap import _ExitedFakeProc


@pytest.mark.parametrize("agent_id", [None, "pi-agent"])
def test_pi_agent_id_does_not_hide_live_spend(tmp_path, monkeypatch, agent_id):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    reservation, reason = reserve_call_budget(
        call_id="pi-review", project_root=None, mission_id=None, provider="pi",
        model="test-model", run_label="engineer-r1", global_root=tmp_path,
    )
    assert reservation is not None and not reason
    monitor = LiveBudgetMonitor(SimpleNamespace(
        backend=SimpleNamespace(_backend_name="pi", _is_copilot=False),
        cost_reservation=reservation, resume_thread_id=None,
    ))
    assert monitor.check() is None
    event = {"type": "message_end", "agentId": agent_id, "message": {
        "role": "assistant", "usage": {
            "input": 10, "output": 3, "cost": {"total": 10},
        },
    }}
    monitor.observe("engineer-r1.stdout", json.dumps(event))
    assert "global daily budget exhausted" in (monitor.check() or "")
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["in_flight_cost_usd"] == 10
    assert monitor.pi_tokens == 13


def test_copilot_child_agent_cannot_replace_parent_session():
    monitor = LiveBudgetMonitor(SimpleNamespace(
        backend=SimpleNamespace(_backend_name="copilot", _is_copilot=True),
        resume_thread_id="parent",
    ))
    for event in (
        {"type": "session.start", "data": {"sessionId": "child"}},
        {"type": "result", "sessionId": "child"},
    ):
        monitor.observe("stdout", json.dumps(dict(event, agentId="nested")))
    assert monitor.session_id == "parent"
    assert monitor.reason == ""


def test_probe_uses_dispatch_executable_environment_and_launch_options(monkeypatch):
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")
    options = RunnerOptions(_bind_provider_session=lambda *_: None)
    resolved = "/fixture/npm/copilot.cmd"
    child_env = {"PATH": "/fixture/node", "COPILOT_HOME": "/fixture/isolated"}
    monkeypatch.setattr(runner, "_resolve_executable", lambda _: resolved)
    environments = []

    def environment(actual_options, *, executable):
        environments.append((actual_options, executable))
        return child_env

    monkeypatch.setattr(runner, "_child_env", environment)

    def probe(command, **kwargs):
        assert command == [resolved, "--help"]
        assert kwargs["env"] == child_env
        assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"
        assert kwargs["timeout"] == 10
        from argus.agent_cli._process_control import background_subprocess_kwargs
        for key, value in background_subprocess_kwargs().items():
            if key == "startupinfo":
                # STARTUPINFO instances have identity, not value equality.
                assert kwargs[key].dwFlags == value.dwFlags
                assert kwargs[key].wShowWindow == value.wShowWindow
            else:
                assert kwargs[key] == value
        return SimpleNamespace(returncode=0, stdout="  --session-id <id>\n")

    monkeypatch.setattr(_copilot_session.subprocess, "run", probe)
    _copilot_session.prepare_session(runner, options, None)
    assert environments == [(options, resolved)]


def test_stop_during_help_probe_prevents_binding_and_spawn(monkeypatch, tmp_path):
    runner, options = setup_runner(monkeypatch, tmp_path, _ExitedFakeProc([]))
    stopped = False

    def probe(*args, **kwargs):
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(_copilot_session, "supports_session_id", probe)
    monkeypatch.setattr(_run_exec, "spawn_owned_process",
                        lambda *a, **kw: pytest.fail("must not spawn after stop"))
    options.external_interrupt_reason_provider = lambda: "operator stop" if stopped else None
    result = runner.run_exec(prompt="fixture", resume_thread_id=None, options=options)
    assert "refused before start: operator stop" == result.fatal_error
    assert result.turn_failed and not result.turn_completed
    assert not read_bindings(tmp_path)["decisions"]


@pytest.mark.parametrize("row_has_identity", [False, True])
def test_history_conflict_skips_only_affected_call(tmp_path, monkeypatch, row_has_identity):
    from argus.core import usage
    from argus.core.provider_sessions import bind_before_dispatch
    from tests.test_live_budget_monitor import _usage_database

    database = tmp_path / "copilot" / "session-store.db"
    _usage_database(database)
    monkeypatch.setenv("COPILOT_HOME", str(database.parent))
    with sqlite3.connect(database) as connection:
        for session in ("healthy", "conflicting", "unrelated"):
            connection.execute(
                "INSERT INTO assistant_usage_events "
                "(session_id, model, total_nano_aiu, created_at) VALUES (?, ?, ?, ?)",
                (session, "gpt-5.6-sol", 100_000_000_000, "1970-01-01T00:00:01.500Z"),
            )
    queried = []
    lookup = usage.find_copilot_usage_near

    def track_lookup(**kwargs):
        queried.append(kwargs["session_id"])
        return lookup(**kwargs)

    monkeypatch.setattr(usage, "find_copilot_usage_near", track_lookup)
    ledger = usage.UsageLedger(tmp_path, migrate_legacy=False)
    for call_id in ("conflicting", "healthy"):
        bind_before_dispatch(tmp_path, call_id=call_id, session_id=call_id, resumed=False)
        ledger.append(usage.build_usage_record(
            call_id=call_id, project_root=tmp_path, mission_id=None,
            provider="copilot", model="gpt-5.6-sol", run_label="engineer-r1",
            started_at=1.0, completed_at=2.0, status="error",
            thread_id=call_id if row_has_identity else None,
        ))
    # A later matching event cannot erase the conflict seen earlier.
    (tmp_path / "events.jsonl").write_text("".join(json.dumps({
        "type": "agent.io.complete", "call_id": "conflicting", "thread_id": identity,
    }) + "\n" for identity in ("unrelated", "conflicting")))
    assert ledger.ensure_copilot_usage_reconciled() == 1
    assert queried == ["healthy"]
    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert rows[0]["cost_usd"] is None and rows[0]["pricing_status"] != "not_billed"
    assert rows[1]["cost_usd"] == 1 and rows[1]["pricing_status"] == "priced"


def test_probe_repairs_node_wrapper_path_using_real_child_env(tmp_path, monkeypatch):
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")
    wrapper = tmp_path / "npm" / "copilot.cmd"
    wrapper.parent.mkdir()
    wrapper.write_text("@node copilot.js")
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    (node_dir / "node.exe").write_text("offline fixture, never executed")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setenv("NVM_SYMLINK", str(node_dir))
    monkeypatch.setattr(runner, "_resolve_executable", lambda _: str(wrapper))
    options = RunnerOptions(_bind_provider_session=lambda *_: None)

    def probe(command, **kwargs):
        assert command == [str(wrapper), "--help"]
        assert kwargs["env"]["PATH"].split(os.pathsep)[0] == str(node_dir)
        assert kwargs["env"] == runner._child_env(options, executable=str(wrapper))
        assert kwargs["env"]["COPILOT_HOME"]
        return SimpleNamespace(returncode=0, stdout="  --session-id <id>\n")

    monkeypatch.setattr(_copilot_session.subprocess, "run", probe)
    _copilot_session.prepare_session(runner, options, None)
