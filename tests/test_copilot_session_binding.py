"""The agent-CLI backend binds a NEW Copilot session to the call before spawn
(issue #129) and keeps that binding on every exit path.

Synthetic identities, a faked runner boundary, and a stubbed session-store
reader: no provider call, no real CLI, no spend. Identity never implies
completion, a resumed call keeps its original session, and a CLI that reports
another session leaves accounting unresolved rather than charging it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.agent_cli_backend._budget_monitor import LiveBudgetMonitor
from argus.agent_cli.models import AgentRunResult
from argus.core.models import RunnerOptions

UUID_LEN = 36
ORIGINAL = "sess-original"


def _cli_result(
    *, thread_id: str | None, exit_code: int = 0, fatal_error: str | None = None,
    turn_completed: bool | None = None, conflict: str | None = None,
) -> AgentRunResult:
    failed = exit_code != 0 or bool(fatal_error) or bool(conflict)
    return AgentRunResult(
        command=["copilot"], exit_code=exit_code, thread_id=thread_id,
        agent_messages=["ok"] if not failed else [],
        # The provider streamed real work before the call ended (#129 shape).
        stdout_lines=['{"type":"model.call_finished"}'], stdout_line_count=1,
        turn_completed=(not failed) if turn_completed is None else turn_completed,
        turn_failed=failed, fatal_error=fatal_error, session_identity_conflict=conflict,
    )


@pytest.fixture
def copilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A Copilot backend whose CLI advertises ``--session-id`` and whose
    session store yields nothing (the interrupted-call shape)."""
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.copilot_cli_supports_session_id",
        lambda executable: True,
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: object(),
    )
    lookups: list[str | None] = []

    def read_usage(cursor, *, session_id):  # noqa: ARG001
        lookups.append(session_id)
        return None

    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since", read_usage,
    )
    backend = AgentCliBackend(backend="copilot")
    seen: dict[str, Any] = {"lookups": lookups, "log_path": log_path}

    def install(fake):
        def run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
            seen["options"] = kwargs["options"]
            seen["resume_thread_id"] = kwargs["resume_thread_id"]
            seen["start_rows_at_spawn"] = _rows(log_path, "agent.io.start")
            return fake(kwargs)

        monkeypatch.setattr(backend._runner.__class__, "run_exec", run_exec, raising=True)

    seen["install"] = install
    seen["backend"] = backend
    return seen


def _rows(path: Path, kind: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if row.get("type") == kind]


def _usage_row(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[-1])


def test_new_session_identity_is_persisted_before_spawn_and_survives_an_exception(
    tmp_path: Path, copilot,
) -> None:
    def fake(kwargs):
        raise RuntimeError("provider reader failed mid-call")

    copilot["install"](fake)
    result = copilot["backend"].run_exec(
        prompt="task", options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1",
    )

    bound = copilot["options"].provider_session_id
    assert isinstance(bound, str) and len(bound) == UUID_LEN
    assert copilot["start_rows_at_spawn"][0]["provider_session_id"] == bound, (
        "the binding is durable before the process exists"
    )
    assert result.thread_id == bound
    assert result.fatal_error.startswith("RuntimeError:")
    usage = _usage_row(tmp_path)
    assert usage["status"] == "error" and usage["thread_id"] == bound
    assert usage["cost_usd"] is None or usage["pricing_status"] != "priced"


def test_watchdog_kill_without_terminal_result_keeps_identity_and_stays_failed(
    tmp_path: Path, copilot,
) -> None:
    def fake(kwargs):
        return _cli_result(
            thread_id=kwargs["options"].provider_session_id, exit_code=-9,
            fatal_error="Provider turn cap reached: this engineer-r1 call used 40 provider turns",
        )

    copilot["install"](fake)
    result = copilot["backend"].run_exec(
        prompt="task", options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1",
    )

    bound = copilot["options"].provider_session_id
    assert copilot["lookups"] == [bound], "usage is read under the bound session"
    assert result.thread_id == bound
    assert result.fatal_error.startswith("Provider turn cap reached")
    complete = _rows(copilot["log_path"], "agent.io.complete")[-1]
    assert complete["thread_id"] == bound and complete["provider_session_id"] == bound
    assert complete["turn_completed"] is False and complete["turn_failed"] is True
    usage = _usage_row(tmp_path)
    assert usage["status"] == "error" and usage["thread_id"] == bound
    assert usage["pricing_status"] in {"partial", "unpriced", "pending"} or usage["cost_usd"] is None
    assert "accounting_pending" not in str(usage.get("error") or ""), (
        "with the identity retained, the cause is no longer a lost session"
    )


def test_resumed_call_keeps_its_original_identity_and_gets_no_replacement(
    tmp_path: Path, copilot,
) -> None:
    copilot["install"](lambda kwargs: _cli_result(thread_id=None, exit_code=-9, fatal_error="killed"))
    result = copilot["backend"].run_exec(
        prompt="continue", options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="engineer-r2", resume_thread_id=ORIGINAL,
    )

    assert copilot["options"].provider_session_id is None
    assert copilot["resume_thread_id"] == ORIGINAL
    assert copilot["start_rows_at_spawn"][0]["provider_session_id"] is None
    assert copilot["start_rows_at_spawn"][0]["resume_thread_id"] == ORIGINAL
    assert copilot["lookups"] == [ORIGINAL]
    assert result.thread_id == ORIGINAL and _usage_row(tmp_path)["thread_id"] == ORIGINAL


def test_warm_acp_path_owns_its_session_and_is_not_prebound(tmp_path: Path, copilot, monkeypatch) -> None:
    monkeypatch.setattr(
        copilot["backend"]._runner.__class__, "_acp_enabled", lambda self, label, options=None: True,
    )
    copilot["install"](lambda kwargs: _cli_result(thread_id="acp-session"))
    result = copilot["backend"].run_exec(
        prompt="hi", options=RunnerOptions(working_dir=str(tmp_path)), run_label="manager-chat",
    )
    assert copilot["options"].provider_session_id is None
    assert result.thread_id == "acp-session"


def test_cli_without_session_id_flag_is_reported_and_lost_identity_is_named(
    tmp_path: Path, copilot, monkeypatch, caplog,
) -> None:
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.copilot_cli_supports_session_id",
        lambda executable: False,
    )
    copilot["install"](lambda kwargs: _cli_result(thread_id=None, exit_code=-9, fatal_error="killed by watchdog"))
    with caplog.at_level(logging.WARNING, logger="argus.adapters.agent_cli_backend._exec_spawn"):
        result = copilot["backend"].run_exec(
            prompt="task", options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1",
        )

    assert copilot["options"].provider_session_id is None
    assert any("does not advertise --session-id" in rec.getMessage() for rec in caplog.records)
    assert result.thread_id is None and result.fatal_error == "killed by watchdog"
    usage = _usage_row(tmp_path)
    assert usage["thread_id"] is None and usage["status"] == "error"
    assert "accounting_pending: lost_session_identity" in usage["error"]
    assert usage["cost_usd"] is None or usage["pricing_status"] != "priced"


def test_reported_identity_mismatch_keeps_accounting_unresolved(tmp_path: Path, copilot) -> None:
    conflict = "Copilot session identity mismatch: result reported sessionId 'other'"
    copilot["install"](lambda kwargs: _cli_result(thread_id=None, conflict=conflict))
    result = copilot["backend"].run_exec(
        prompt="continue", options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="engineer-r2", resume_thread_id=ORIGINAL,
    )

    assert copilot["lookups"] == [None], "no session is looked up for a conflicted call"
    assert result.thread_id is None, "the resume identity must not be re-bound either"
    complete = _rows(copilot["log_path"], "agent.io.complete")[-1]
    assert complete["thread_id"] is None and complete["session_identity_conflict"] == conflict
    usage = _usage_row(tmp_path)
    assert usage["thread_id"] is None and usage["status"] == "error"
    assert "accounting_pending: session_identity_conflict" in usage["error"]


def test_live_budget_monitor_reads_the_prebound_session_before_any_event(monkeypatch) -> None:
    lookups: list[str | None] = []

    def read_usage(cursor, *, session_id, timeout):  # noqa: ARG001
        lookups.append(session_id)
        return None

    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._budget_monitor.read_copilot_usage_since", read_usage,
    )
    ctx = SimpleNamespace(
        resume_thread_id=None, provider_session_id="prebound-session",
        backend=SimpleNamespace(_is_copilot=True, _backend_name="copilot"),
        copilot_usage_cursor=object(), options=SimpleNamespace(model=None),
        cost_reservation=SimpleNamespace(observe_cost=lambda observed, tokens: None),
    )
    monitor = LiveBudgetMonitor(ctx, interval_seconds=0)
    assert monitor.check() is None
    assert lookups == ["prebound-session"]
