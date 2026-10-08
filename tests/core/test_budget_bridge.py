"""One Python owner protects TypeScript calls with existing budget/ledger locks."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

from argus.adapters.budget_bridge import CONTRACT, BudgetSession
from argus.core.cost_control import (
    cost_admission_reason,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.token_usage import extract_token_usage
from argus.core.usage import UsageLedger
from argus.core.windows_job import spawn_owned_process, terminate_owned_process


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "0")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_TOKEN_CAP_CACHED_WEIGHT", "1")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    (tmp_path / "projects/p").mkdir(parents=True)
    return tmp_path


def observation(cost=0.25, tokens=100):
    usage = extract_token_usage([{"usage": {"input_tokens": tokens, "output_tokens": 10}}])
    return {"usage": {**asdict(usage), "observed": usage.observed, "complete": usage.complete},
            "pricing": {"cost_usd": cost, "status": "priced", "tier": "default", "reason": ""}, "error": None}


PARAMS = {"sid": "p", "model": "openai/gpt-5.6-sol", "run_label": "typescript-pi", "mission_id": None}


def start(root):
    session = BudgetSession(root)
    assert session.dispatch("reserve", PARAMS)["admitted"]
    assert session.dispatch("start", {})["started"]
    return session


def test_active_marker_does_not_block_or_double_count_a_live_call(root):
    session = start(root)
    try:
        assert session.dispatch("observe", {"accounting": observation()}) == {"stop_reason": ""}
        view = cost_control_snapshot(global_root=root)
        assert view["in_flight_cost_usd"] == 0.25
        assert view["blocking_unresolved_calls"] == 0
        another, reason = reserve_call_budget(
            call_id="python-call", project_root=root / "projects/p", mission_id=None,
            provider="pi", model="model", run_label="other", global_root=root,
        )
        assert another is not None and reason == ""
        another.release()
    finally:
        session.close()
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)
    view = cost_control_snapshot(global_root=root)
    assert view["unacknowledged_observed_cost_usd"] == 0.25
    assert view["in_flight_cost_usd"] == 0


def test_settlement_is_durable_before_the_pending_marker_is_removed(root):
    session = start(root)
    try:
        session.dispatch("observe", {"accounting": observation()})
        result = session.dispatch("settle", {"accounting": observation(), "completed": True, "thread_id": "pi-session"})
        assert result["settlement"] == "settled"
        with pytest.raises(ValueError):
            session.dispatch("settle", {"accounting": observation(), "completed": True, "thread_id": None})
    finally:
        session.close()
    rows = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert len(rows) == 1 and rows[0].cost_usd == 0.25
    assert rows[0].cost_basis == "typescript_pi" and rows[0].thread_id == "pi-session"
    assert rows[0].input_tokens == 100 and rows[0].pricing_status == "priced"
    assert not list((root / "cost-control.failed").glob("*.json"))
    assert cost_admission_reason(global_root=root) == ""


def test_cancelled_call_keeps_known_cost_and_remains_unresolved(root):
    session = start(root)
    try:
        result = session.dispatch("settle", {"accounting": observation(), "completed": False, "thread_id": None})
        assert result["settlement"] == "unresolved"
    finally:
        session.close()
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == 0.25 and row.pricing_status == "partial"
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)


@pytest.mark.parametrize("knob,value,message", [
    ("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0.2", "daily budget"),
    ("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "100", "token budget"),
])
def test_live_cost_and_token_caps_use_the_existing_budget_policy(root, monkeypatch, knob, value, message):
    monkeypatch.setenv(knob, value)
    session = start(root)
    try:
        assert message in session.dispatch("observe", {"accounting": observation()})["stop_reason"]
    finally:
        session.close()


def test_unstarted_session_releases_without_unknown_liability(root):
    session = BudgetSession(root)
    assert session.dispatch("reserve", PARAMS)["admitted"]
    session.close()
    assert cost_admission_reason(global_root=root) == ""
    assert not (root / "projects/p/usage.jsonl").exists()


def test_ledger_failure_preserves_marker_and_blocks_later_admission(root, monkeypatch):
    session = start(root)
    session.dispatch("observe", {"accounting": observation()})
    monkeypatch.setattr(UsageLedger, "append", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    try:
        with pytest.raises(OSError):
            session.dispatch("settle", {"accounting": observation(), "completed": True, "thread_id": None})
    finally:
        session.close()
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)
    assert cost_control_snapshot(global_root=root)["unacknowledged_observed_cost_usd"] == 0.25


@pytest.mark.parametrize("field,value", [("sid", "../escape"), ("model", ""), ("mission_id", {})])
def test_invalid_admission_never_starts_a_reservation(root, field, value):
    session = BudgetSession(root)
    with pytest.raises(ValueError):
        session.dispatch("reserve", {**PARAMS, field: value})
    session.close()
    assert cost_control_snapshot(global_root=root)["active_reservations"] == 0


@pytest.mark.parametrize("observed", [False, True])
def test_killed_budget_owner_exposes_its_marker_to_normal_python_admission(root, observed):
    # Windows venv python.exe is a launcher, whose PID can differ from the
    # interpreter holding the budget marker. Own and terminate the whole tree.
    process = spawn_owned_process(
        [sys.executable, "-m", "argus.adapters.budget_bridge", "--global-root", str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )
    responses = queue.Queue()
    thread = threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True)
    thread.start()
    owner_api = None
    owner_handle = None
    try:
        commands = [("reserve", PARAMS), ("start", {})]
        if observed:
            commands.append(("observe", {"accounting": observation()}))
        for sequence, (method, params) in enumerate(commands, start=1):
            process.stdin.write(json.dumps({"protocol": CONTRACT["protocol"], "version": CONTRACT["version"],
                "id": sequence, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            assert json.loads(responses.get(timeout=10))["ok"]
        assert cost_admission_reason(global_root=root) == ""
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as w

            state = json.loads((root / "cost-control.json").read_text())
            owner_pid = state["reservations"][0]["pid"]
            assert owner_pid != os.getpid()
            owner_api = ctypes.WinDLL("kernel32", use_last_error=True)
            owner_api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            owner_api.OpenProcess.restype = w.HANDLE
            owner_api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            owner_api.WaitForSingleObject.restype = w.DWORD
            owner_api.CloseHandle.argtypes = [w.HANDLE]
            owner_api.CloseHandle.restype = w.BOOL
            owner_handle = owner_api.OpenProcess(0x100000 | 0x1000, False, owner_pid)
            assert owner_handle, ctypes.WinError(ctypes.get_last_error())
            member = w.BOOL()
            job = process._argus_windows_job
            assert job.api.IsProcessInJob(owner_handle, job.handle, ctypes.byref(member)) and member.value
            assert terminate_owned_process(process) is True
            assert owner_api.WaitForSingleObject(owner_handle, 5000) == 0
        else:
            process.kill()
            process.wait(timeout=5)
        assert "unresolved provider cost" in cost_admission_reason(global_root=root)
        view = cost_control_snapshot(global_root=root)
        assert view["unacknowledged_observed_cost_usd"] == (0.25 if observed else 0)
        assert view["blocking_unresolved_calls"] == 1
        assert view["in_flight_cost_usd"] == 0
    finally:
        try:
            if os.name == "nt":
                assert terminate_owned_process(process) is True
            elif process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            thread.join(timeout=2)
        finally:
            if owner_handle:
                assert owner_api.CloseHandle(owner_handle)


def test_pending_marker_survives_failed_fsync_before_start_ack(root, monkeypatch):
    from argus.core import cost_control

    session = BudgetSession(root)
    assert session.dispatch("reserve", PARAMS)["admitted"]

    def no_space(_fd):
        raise OSError("no space")

    with monkeypatch.context() as patch:
        patch.setattr(cost_control.os, "fsync", no_space)
        with pytest.raises(OSError):
            session.dispatch("start", {})
    session.close()
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)


def test_next_local_day_observation_does_not_recharge_prior_day_tokens(root, monkeypatch):
    from argus.adapters import budget_bridge

    session = start(root)
    seen = []
    try:
        monkeypatch.setattr(session.reservation, "observe_cost", lambda cost, **kw: seen.append((cost, kw["tokens"])) or "")
        session.dispatch("observe", {"accounting": observation(1, 100)})
        monkeypatch.setattr(budget_bridge.time, "strftime", lambda *_: "next-local-day")
        session.dispatch("observe", {"accounting": observation(1.5, 120)})
        assert seen == [(1, 110), (0.5, 20)]
    finally:
        session.close()


@pytest.mark.parametrize("bad", [float("inf"), -1, True])
def test_invalid_costs_cannot_be_persisted_as_a_settlement(root, bad):
    session = start(root)
    try:
        with pytest.raises(ValueError):
            session.dispatch("settle", {"accounting": observation(bad), "completed": True, "thread_id": None})
    finally:
        session.close()
    assert not (root / "projects/p/usage.jsonl").exists()
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)


def test_marker_lock_error_is_not_mistaken_for_a_live_owner(root, monkeypatch):
    import portalocker

    from argus.core import cost_control

    session = start(root)
    session.close()

    def unsupported(*_args):
        raise portalocker.exceptions.LockException("unsupported filesystem lock")

    monkeypatch.setattr(cost_control.portalocker, "lock", unsupported)
    rows = cost_control._failed_finalization_rows(root)
    assert len(rows) == 1 and rows[0][1]["blocking"]


def test_marker_retired_during_directory_scan_does_not_create_a_phantom_liability(root, monkeypatch):
    from argus.core import cost_control

    session = start(root)
    session.close()
    directory = root / "cost-control.failed"
    original = Path.iterdir

    def retiring(path):
        rows = list(original(path))
        if path == directory:
            for item in rows:
                item.unlink()
        return iter(rows)

    monkeypatch.setattr(Path, "iterdir", retiring)
    assert cost_control._failed_finalization_rows(root) == []


def _delay_marker_unlock(root, monkeypatch):
    import portalocker

    from argus.core import cost_control

    original_lock = cost_control.portalocker.lock

    def delayed(handle, flags):
        if not isinstance(handle, int) and Path(handle.name).parent == root / "cost-control.failed":
            raise portalocker.exceptions.AlreadyLocked("Windows has not released the marker lock yet")
        return original_lock(handle, flags)

    monkeypatch.setattr(cost_control.portalocker, "lock", delayed)
    return cost_control


@pytest.mark.parametrize("observed", [False, True])
def test_dead_owner_blocks_immediately_while_windows_marker_unlock_is_delayed(root, monkeypatch, observed):
    session = start(root)
    try:
        if observed:
            session.dispatch("observe", {"accounting": observation()})
        session.pending.close()
        costs = _delay_marker_unlock(root, monkeypatch)
        monkeypatch.setattr(costs, "_pid_alive", lambda _pid: False)
        assert "unresolved provider cost" in cost_admission_reason(global_root=root)
        view = cost_control_snapshot(global_root=root)
        assert view["blocking_unresolved_calls"] == 1
        assert view["unacknowledged_observed_cost_usd"] == (0.25 if observed else 0)
        assert view["in_flight_cost_usd"] == 0
        denied, reason = reserve_call_budget(
            call_id="next-call", project_root=root / "projects/p", mission_id=None,
            provider="pi", model="model", run_label="other", global_root=root,
        )
        assert denied is None and "unresolved provider cost" in reason
        assert cost_control_snapshot(global_root=root)["unresolved"][0]["call_id"] == session.call_id
    finally:
        session.close()


def test_locked_marker_with_confirmed_live_owner_remains_in_flight(root, monkeypatch):
    session = start(root)
    try:
        session.dispatch("observe", {"accounting": observation()})
        _delay_marker_unlock(root, monkeypatch)
        assert cost_admission_reason(global_root=root) == ""
        view = cost_control_snapshot(global_root=root)
        assert view["blocking_unresolved_calls"] == 0
        assert view["in_flight_cost_usd"] == 0.25
    finally:
        session.close()


def test_newer_marker_observation_updates_the_delayed_unlock_lower_bound(root, monkeypatch):
    session = start(root)
    try:
        # Crash between the marker's durable write and the reservation update.
        session.pending.observe(0.25, 110)
        session.pending.close()
        with monkeypatch.context() as patch:
            costs = _delay_marker_unlock(root, patch)
            patch.setattr(costs, "_pid_alive", lambda _pid: False)
            assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1
        view = cost_control_snapshot(global_root=root)
        assert view["unacknowledged_observed_cost_usd"] == 0.25
        assert view["blocking_unresolved_calls"] == 1
        assert view["unsettled_tokens"] == 110
    finally:
        session.close()


@pytest.mark.parametrize("unrelated", [False, True])
def test_live_owner_metadata_recovery_retires_only_its_temporary_alias(root, monkeypatch, unrelated):
    from argus.core import cost_control as costs

    session = start(root)
    try:
        state = costs._read_state(root, costs.time.time())
        state["reservations"] = []
        if unrelated:
            state["unresolved"].append({"call_id": "unrelated", "project_id": "p", "pricing_status": "unknown"})
        costs._write_state(root, state, costs.time.time())
        _delay_marker_unlock(root, monkeypatch)
        assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == (2 if unrelated else 1)
        session.dispatch("observe", {"accounting": observation()})
        reason = cost_admission_reason(global_root=root)
        assert ("unresolved provider cost" in reason) if unrelated else (reason == "")
        view = cost_control_snapshot(global_root=root)
        assert view["blocking_unresolved_calls"] == (1 if unrelated else 0)
        assert view["in_flight_cost_usd"] == 0.25
        assert {row["call_id"] for row in view["unresolved"]} == ({"unrelated"} if unrelated else set())
    finally:
        session.close()


@pytest.mark.parametrize("state_missing", [False, True])
def test_unknown_locked_marker_alias_recovers_after_metadata_becomes_readable(root, monkeypatch, state_missing):
    from argus.core import cost_control as costs
    from argus.core.usage import build_usage_record

    session = start(root)
    try:
        session.dispatch("observe", {"accounting": observation()})
        session.pending.close()
        state = costs._read_state(root, costs.time.time())
        state["reservations"] = []
        if state_missing:
            # Day rollover returns the same empty owner projection.
            state["day"] = "previous-day"
        costs._write_state(root, state, costs.time.time())
        if state_missing:
            payload = json.loads((root / costs.COST_CONTROL_STATE_FILE).read_text())
            payload["day"] = "previous-day"
            (root / costs.COST_CONTROL_STATE_FILE).write_text(json.dumps(payload))
        with monkeypatch.context() as patch:
            _delay_marker_unlock(root, patch)
            assert "unresolved provider cost" in cost_admission_reason(global_root=root)
            assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1
        view = cost_control_snapshot(global_root=root)
        assert view["blocking_unresolved_calls"] == 1
        assert view["unresolved"][0]["call_id"] == session.call_id
        assert view["unacknowledged_observed_cost_usd"] == 0.25
        record = build_usage_record(
            call_id=session.call_id, project_root=root / "projects/p", mission_id=None,
            provider="pi", model="model", run_label="other", started_at=session.started_at,
            completed_at=costs.time.time(), status="completed",
            token_usage=extract_token_usage([{"usage": {"input_tokens": 100, "output_tokens": 10}}]),
            provider_cost_usd=0.25,
        )
        UsageLedger(root / "projects/p", migrate_legacy=False).append(record)
        assert cost_admission_reason(global_root=root) == ""
        assert cost_control_snapshot(global_root=root)["unresolved_calls"] == 0
    finally:
        session.close()


def test_settled_marker_with_delayed_retirement_does_not_leave_a_permanent_alias(root, monkeypatch):
    from argus.core import cost_control as costs

    session = start(root)
    try:
        marker = session.pending.path
        original_unlink = Path.unlink

        def delayed_unlink(path, *args, **kwargs):
            if path == marker:
                raise PermissionError("Windows is still retiring the marker")
            return original_unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", delayed_unlink)
            with pytest.raises(PermissionError):
                session.dispatch("settle", {"accounting": observation(), "completed": True, "thread_id": None})
            _delay_marker_unlock(root, patch)
            assert "unresolved provider cost" in cost_admission_reason(global_root=root)
            assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1
        assert cost_admission_reason(global_root=root) == ""
        assert cost_control_snapshot(global_root=root)["unresolved_calls"] == 0
        assert not marker.exists()
        assert costs._read_state(root, costs.time.time())["unresolved"] == []
    finally:
        session.close()
