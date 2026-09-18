"""Synthetic only. Actual incident copies stay outside the repository."""
from __future__ import annotations

import errno
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from argus.core import cost_control as costs
from argus.core.accounting_integrity import AccountingIntegrityError, durable_json
from argus.core.dispatch_safety import (
    DispatchSafetyError,
    assert_project_dispatch,
    provider_dispatch_guard,
    quiesce_project,
    safety_snapshot,
)
from argus.core.finalization_intents import assert_finalization_integrity
from argus.core.usage import UsageLedger, UsageRecord, _rewrite_usage_rows
from argus.life.memory import Backlog, BacklogItem


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    p = tmp_path / "projects" / "synthetic-project"
    p.mkdir(parents=True)
    return p


def reserve(root, project, call="synthetic-call"):
    return costs.reserve_call_budget(call_id=call, project_root=project,
        mission_id="synthetic-mission", provider="codex", model="synthetic-model",
        run_label="planner", global_root=root)


def receipt(project, call="synthetic-call"):
    return UsageRecord.from_jsonable({"call_id": call, "project_id": project.name,
        "provider": "pi", "model": "synthetic-model", "run_label": "engineer",
        "status": "completed", "pricing_status": "priced", "cost_usd": 1.25,
        "started_at": time.time()-1, "completed_at": time.time()})


@pytest.mark.parametrize("raw", [b'{"call_id":"broken', b'{"call_id":"broken'+b'{"call_id":"suffix"}\n',
                                    b'[]\n', b'{"call_id":"complete"}', b'\xff\n', b'{"call_id":"nan","cost_usd":NaN}\n'])
def test_corruption_blocks_before_reservation_and_rewrite(tmp_path, project, raw):
    tape = project / "usage.jsonl"
    tape.write_bytes(raw)
    for operation in [lambda: reserve(tmp_path, project),
                      lambda: costs.cost_admission_reason(global_root=tmp_path),
                      lambda: costs.cost_control_snapshot(global_root=tmp_path),
                      lambda: UsageLedger(project).ensure_copilot_usage_reconciled(),
                      lambda: _rewrite_usage_rows(tape, []),
                      lambda: UsageLedger(project).append(receipt(project))]:
        with pytest.raises(AccountingIntegrityError):
            operation()
        assert tape.read_bytes() == raw
        assert not (tmp_path / costs.COST_CONTROL_STATE_FILE).exists()


def test_global_damage_cannot_be_swallowed_as_other_project(tmp_path, project):
    other = tmp_path / "projects" / "other"
    other.mkdir()
    (other / "usage.jsonl").write_bytes(b'{"call_id":"broken')
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project)


def test_dead_zero_observation_reservation_and_midnight_preserve_debt(tmp_path, project):
    reservation, _ = reserve(tmp_path, project)
    assert reservation
    reservation.intent.detach()  # simulate process/thread exit, not billing evidence
    before = (tmp_path / costs.COST_CONTROL_STATE_FILE).read_bytes()
    with patch.object(costs, "_pid_alive", return_value=False):
        snapshot = costs.cost_control_snapshot(global_root=tmp_path, now=time.time()+86400)
        with pytest.raises(AccountingIntegrityError):
            costs.cost_admission_reason(global_root=tmp_path, now=time.time()+86400)
    assert snapshot["active_reservations"] == 1
    assert (tmp_path / costs.COST_CONTROL_STATE_FILE).read_bytes() == before
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, "next")


def test_legacy_live_owner_does_not_prove_active_provider(tmp_path, project):
    state = costs._default_state(time.time())
    state["version"] = 2
    state["reservations"] = [{"id": "old", "call_id": "old", "pid": os.getpid(),
                              "project_root": str(project), "observed_cost_usd": 0}]
    durable_json(tmp_path / costs.COST_CONTROL_STATE_FILE, state)
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project)


@pytest.mark.parametrize("fault", ["fsync", "replace"])
def test_durable_write_faults_preserve_previous_bytes(tmp_path, fault):
    target = tmp_path / "state.json"
    durable_json(target, {"old": True})
    before = target.read_bytes()
    with patch("argus.core.safety_io.os."+fault,
               side_effect=OSError(errno.ENOSPC, "synthetic full disk")):
        with pytest.raises(OSError):
            durable_json(target, {"new": True})
    assert target.read_bytes() == before


def test_directory_fsync_failure_does_not_report_commit_success(tmp_path):
    with patch("argus.core.safety_io.fsync_directory",
               side_effect=OSError(errno.ENOSPC, "synthetic directory fsync failure")):
        with pytest.raises(OSError):
            durable_json(tmp_path / "state.json", {"paused": True})
    # Rename may already be visible; caller must re-read, not claim success.
    assert json.loads((tmp_path / "state.json").read_text()) == {"paused": True}


def test_intent_failure_retains_receipt_errors_and_blocks_live_owner(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    original = receipt(project)
    r.prepare_finalization(original, error="original provider error")
    with patch.object(costs, "_write_state", side_effect=OSError(errno.ENOSPC, "synthetic")):
        with pytest.raises(OSError):
            r.settle(original)
    r.finalization_failed("settlement failed: synthetic ENOSPC")
    intent = json.loads(r.intent.path.read_text())
    assert intent["receipts"] == [original.to_jsonable()]
    assert "original provider error" in intent["errors"]
    assert intent["phase"] == "failed"
    assert json.loads((tmp_path / costs.COST_CONTROL_STATE_FILE).read_text())["reservations"]
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, "successor")


def test_even_failure_journal_enospc_releases_lease_and_preserves_original_obligation(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    original = r.intent.path.read_bytes()
    with patch("argus.core.finalization_intents.durable_json", side_effect=OSError(errno.ENOSPC, "synthetic")):
        with pytest.raises(OSError):
            r.finalization_failed("failed before error could be saved")
    assert r.intent.path.read_bytes() == original
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, "next")


def test_active_leases_allow_parallel_calls_but_completion_is_once(tmp_path, project):
    a, _ = reserve(tmp_path, project, "a")
    b, _ = reserve(tmp_path, project, "b")
    assert a and b
    ledger = UsageLedger(project)
    record = receipt(project, "a")
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(lambda _: ledger.append(record), range(2))) == 1
    assert a.settle(record)
    assert not a.settle(record)
    b.release()
    assert costs.cost_control_snapshot(global_root=tmp_path)["active_reservations"] == 0
    assert ledger.summary().known_cost_usd == 1.25
    assert_finalization_integrity(tmp_path, [])


def test_quiesce_is_cas_sticky_and_does_not_mutate_any_debt_or_evidence(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    tape = project / "usage.jsonl"
    tape.write_bytes(b'{"call_id":"truncated')
    before = {p: p.read_bytes() for p in [tape, tmp_path / costs.COST_CONTROL_STATE_FILE, r.intent.path]}
    fenced = quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="integrity investigation")
    assert fenced["quiescent"] and fenced["paused"] and not fenced["accounting_settled"]
    with pytest.raises(ValueError):
        quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="stale")
    (project / "campaign-state.json").write_text('{"observation":"benign update"}')
    with patch("time.time", return_value=time.time()+7200):
        with pytest.raises(DispatchSafetyError):
            assert_project_dispatch(project)
    r.intent.detach()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    with pytest.raises(DispatchSafetyError):
        reserve(tmp_path, project, "next")


def test_quiesce_reports_inflight_not_stopped_and_no_next_provider_round(tmp_path, project):
    entered, finish = threading.Event(), threading.Event()
    def inflight():
        with provider_dispatch_guard(project):
            entered.set()
            finish.wait(3)
    thread = threading.Thread(target=inflight)
    thread.start()
    assert entered.wait(2)
    try:
        fenced = quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="stop dispatch")
        assert not fenced["quiescent"] and not fenced["daemon_stopped"]
        with pytest.raises(DispatchSafetyError):
            with provider_dispatch_guard(project):
                pytest.fail("new provider work")
    finally:
        finish.set()
        thread.join(3)
    assert safety_snapshot(project)["paused"]


def test_serial_successor_cannot_duplicate_retained_origin_even_with_parallel_override(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    origin = BacklogItem.new(title="Original real work", objective="Retained work")
    origin.status = "running"
    origin.running_owner = "primary"
    origin.attempt = 2
    successor = BacklogItem.new(title="Differently worded continuation", objective="Do not duplicate")
    successor.node_key = origin.id
    backlog._save([origin, successor])
    before = backlog.path.read_bytes()
    assert backlog.next_pending() is None
    assert backlog.claim_next(owner="successor") is None
    assert backlog.claim_next(owner="successor", respect_running=False) is None
    assert backlog.path.read_bytes() == before
    assert [(r.id, r.attempt, r.running_owner) for r in backlog.active() if r.status == "running"] == [(origin.id, 2, "primary")]


def test_real_wait_wake_and_1801_second_expiry_cannot_clear_dispatch_fence(tmp_path, project):
    from types import SimpleNamespace

    from argus.life.supervisor import LifeSupervisor
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="reviewed repair required")
    supervisor = object.__new__(LifeSupervisor)
    supervisor.config = SimpleNamespace(continuous_objective="unchanged")
    supervisor._project_workdir = lambda: project
    watched = project / "campaign-state.json"
    watched.write_text('{"observation":1,"integrity":"unrepaired"}')
    revision = supervisor._planner_waiting_observed_revision(wake_on=["artifact_revision"], watched_paths=[watched.name])
    wait = {"active": True, "wait_mode": "event", "expires_at": 0,
            "observed_revision": revision, "wake_on": ["artifact_revision"], "watched_paths": [watched.name],
            "recheck_condition": "independently reviewed repair", "operator_action_required": False,
            "allow_verification_probe": False}
    supervisor._load_planner_waiting_contract_state = lambda: wait
    supervisor._write_planner_waiting_contract_state = lambda state: True
    supervisor._emit = lambda event: None
    supervisor._reset_idle_backoff = lambda: None
    watched.write_text('{"observation":2,"integrity":"unrepaired"}')
    supervisor._planner_event_wait_outcome()
    assert not wait["active"]  # ordinary scheduling hint CAN wake, not authorize
    with pytest.raises(DispatchSafetyError):
        reserve(tmp_path, project, "after-wake")
    supervisor._planner_visible_input_signature = lambda **kw: "unchanged"
    supervisor._planner_unchanged_skip_signature = "unchanged"
    supervisor._planner_unchanged_skip_armed_at = 0
    with patch("argus.life.supervisor._planning_context.time.monotonic", return_value=1801):
        supervisor._maybe_skip_unchanged_planner_cycle(SimpleNamespace(operator_context_revision=0, inbox_delivery_messages=()))
    with pytest.raises(DispatchSafetyError):
        reserve(tmp_path, project, "after-expiry")
    assert safety_snapshot(project)["epoch"] == 1


@pytest.mark.parametrize("failure_site", ["usage-fsync", "rewrite-fsync", "rewrite-rename"])
def test_usage_io_failures_keep_original_or_complete_receipt_and_never_report_success(tmp_path, project, failure_site):
    ledger = UsageLedger(project)
    original = receipt(project, "first")
    ledger.append(original)
    before = ledger.path.read_bytes()
    function = "os.replace" if failure_site == "rewrite-rename" else "os.fsync"
    with patch("argus.core.usage."+function, side_effect=OSError(errno.ENOSPC, "synthetic write fault")):
        with pytest.raises(OSError):
            if failure_site == "usage-fsync":
                ledger.append(receipt(project, "second"))
            else:
                _rewrite_usage_rows(ledger.path, [original.to_jsonable()])
    if failure_site == "usage-fsync":
        assert ledger.path.read_bytes().startswith(before)
        # A failed fsync is not an acknowledged commit even if bytes are visible.
    else:
        assert ledger.path.read_bytes() == before


def test_settle_cannot_hide_receipt_in_closed_intent_outside_ledger(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    record = receipt(project)
    # Public API must make this receipt durable, not just delete its reservation.
    assert r.settle(record)
    assert UsageLedger(project).summary().known_cost_usd == 1.25
    assert json.loads(r.intent.path.read_text())["receipts"] == [record.to_jsonable()]


def test_conflicting_late_receipt_never_replaces_or_double_charges_canonical(tmp_path, project):
    from dataclasses import replace
    r, _ = reserve(tmp_path, project)
    record = receipt(project)
    ledger = UsageLedger(project)
    ledger.append(record)
    before = ledger.path.read_bytes()
    with pytest.raises(costs.CostControlStateError, match="conflicts"):
        r.settle(replace(record, cost_usd=2.0))
    r.finalization_failed("conflicting duplicate receipt")
    assert ledger.path.read_bytes() == before
    assert ledger.summary().known_cost_usd == 1.25
    with pytest.raises(AccountingIntegrityError):
        costs.cost_admission_reason(global_root=tmp_path)


def test_closed_intent_cannot_hide_retained_unreceipted_reservation(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    data = json.loads(r.intent.path.read_text())
    data["phase"] = "closed"  # inconsistent restored snapshot, NOT a settlement
    durable_json(r.intent.path, data)
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, "next")
