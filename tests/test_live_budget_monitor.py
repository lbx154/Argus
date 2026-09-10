from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from argus_skill.adapters.agent_cli_backend._budget_monitor import (
    LiveBudgetMonitor,
    monitor_budget,
)
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.copilot_acp import CopilotAcpClient
from argus_skill.core.cost_control import cost_control_snapshot, reserve_call_budget
from argus_skill.provider_integrations.copilot_usage import capture_copilot_usage_cursor


def _usage_database(path: Path) -> None:
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE assistant_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                total_nano_aiu INTEGER,
                request_multiplier REAL,
                created_at TEXT
            )
        """)


@pytest.mark.integration
def test_labeled_copilot_subprocess_stops_on_parent_and_child_observed_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1000")
    db = tmp_path / "copilot-home" / "session-store.db"
    _usage_database(db)
    monkeypatch.setenv("COPILOT_HOME", str(db.parent))
    cursor = capture_copilot_usage_cursor()
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = reserve_call_budget(
        call_id="live-call",
        project_root=project,
        mission_id="mission",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
    )
    assert reservation is not None and reason == ""
    events: list[tuple[str, str]] = []
    previous_callback = lambda stream, line: events.append((stream, line))
    runner = AgentCliRunner(
        agent_bin=sys.executable,
        backend="copilot",
        event_callback=previous_callback,
    )
    options = RunnerOptions(watchdog_hard_idle_seconds=10)
    ctx = SimpleNamespace(
        backend=SimpleNamespace(_runner=runner, _is_copilot=True),
        cost_reservation=reservation,
        copilot_usage_cursor=cursor,
        resume_thread_id=None,
    )
    # This is a real isolated subprocess with the Copilot stream/SQLite
    # contract. Both model rows share the parent CLI session, as nested agents
    # do; a different session's much larger charge must never be included.
    script = """
import json, sqlite3, sys, time
from datetime import datetime, timezone
db = sys.argv[1]
def usage(session, model, dollars):
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO assistant_usage_events (session_id, turn_index, model, total_nano_aiu, created_at) VALUES (?, 0, ?, ?, ?)',
                     (session, model, dollars * 100_000_000_000, datetime.now(timezone.utc).isoformat()))
usage('unrelated-session', 'gpt-5.6-sol', 10000)
usage('parent-session', 'gpt-5.6-sol', 600)
print(json.dumps({'type': 'session.start', 'data': {'sessionId': 'parent-session'}}), flush=True)
time.sleep(0.15)
usage('parent-session', 'gpt-5.6-terra', 400)
print(json.dumps({'type': 'assistant.message', 'data': {'content': 'Child review completed.'}}), flush=True)
time.sleep(8)
"""
    command = [sys.executable, "-c", script, str(db)]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=os.name != "nt",
    )
    try:
        started = time.monotonic()
        with monitor_budget(ctx, options):
            state = runner._stream_turn_output(
                process=process,
                command=command,
                options=options,
                run_label="engineer-r1",
                thread_id=None,
            )
        assert time.monotonic() - started < 8
        assert state.watchdog_terminated is True
        assert "global daily budget exhausted ($0.000000 available)" in str(state.watchdog_reason)
        assert process.poll() is not None
        assert any(stream == "engineer-r1.stdout" for stream, _line in events)
        assert cost_control_snapshot(global_root=tmp_path)["in_flight_cost_usd"] == 1000
        assert runner.event_callback is previous_callback
        assert options.external_interrupt_reason_provider is None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_unresolved_settlement_does_not_interrupt_live_call_but_known_cap_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    reservations = []
    for call_id in ("healthy", "unknown"):
        reservation, reason = reserve_call_budget(
            call_id=call_id, project_root=None, mission_id=None, provider="dsh",
            model="test-model", run_label=call_id, global_root=tmp_path,
        )
        assert reservation is not None and reason == ""
        reservations.append(reservation)
    healthy, unknown = reservations
    monitor = LiveBudgetMonitor(
        SimpleNamespace(
            backend=SimpleNamespace(_is_copilot=False),
            cost_reservation=healthy,
            resume_thread_id=None,
        ),
        interval_seconds=0,
    )

    assert monitor.check() is None
    unknown.settle_unknown(reason="provider did not report final usage")
    assert monitor.check() is None
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    assert snapshot["in_flight_cost_usd"] == 0
    assert "global daily budget exhausted" in healthy.observe_cost(10)
    assert "global daily budget exhausted" in monitor.check()


def test_monitor_preserves_callbacks_and_prioritizes_operator_interrupt() -> None:
    previous_callback = Mock()
    previous_interrupt = Mock(return_value="operator requested stop")
    reservation = SimpleNamespace(observe_cost=Mock(return_value="budget exhausted"))
    runner = SimpleNamespace(event_callback=previous_callback)
    options = RunnerOptions(external_interrupt_reason_provider=previous_interrupt)
    ctx = SimpleNamespace(
        backend=SimpleNamespace(_runner=runner, _is_copilot=False),
        cost_reservation=reservation,
        resume_thread_id=None,
    )
    line = json.dumps({"type": "assistant.message", "data": {"content": "Working."}})

    with monitor_budget(ctx, options):
        runner.event_callback("engineer-r1.stdout", line)
        assert options.external_interrupt_reason_provider() == "operator requested stop"
        previous_callback.assert_called_once_with("engineer-r1.stdout", line)
        reservation.observe_cost.assert_not_called()

    assert runner.event_callback is previous_callback
    assert options.external_interrupt_reason_provider is previous_interrupt


def test_monitor_only_learns_parent_session_from_stdout_protocol_events() -> None:
    ctx = SimpleNamespace(resume_thread_id="resumed-parent")
    monitor = LiveBudgetMonitor(ctx)
    monitor.observe("engineer-r1.stderr", json.dumps({
        "type": "session.start", "data": {"sessionId": "stderr-session"},
    }))
    monitor.observe("engineer-r1.stdout", json.dumps({
        "type": "tool.execution_start", "data": {"arguments": {"sessionId": "child-session"}},
    }))
    assert monitor.session_id == "resumed-parent"

    monitor.observe("engineer-r1.stdout", json.dumps({
        "type": "session.start", "data": {"sessionId": "fresh-parent"},
    }))
    assert monitor.session_id == "fresh-parent"


def test_acp_publishes_parent_session_before_prompt_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = LiveBudgetMonitor(SimpleNamespace(resume_thread_id=None))
    client = CopilotAcpClient("unused-offline-copilot")
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_session_for", lambda *_args: "acp-parent")
    seen = []

    def request(method, params, **_kwargs):
        assert method == "session/prompt"
        assert params["sessionId"] == "acp-parent"
        seen.append(monitor.session_id)
        return {"result": {"stopReason": "end_turn"}}

    monkeypatch.setattr(client, "_request", request)
    result = client.run_prompt(
        prompt="offline unit test",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="manager-frontdoor-classify",
        emit=lambda line: monitor.observe("manager-frontdoor-classify.stdout", line),
    )

    assert result.exit_code == 0
    assert seen == ["acp-parent"]
