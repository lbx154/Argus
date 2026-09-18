"""Observed lower bounds must survive partial receipts under both admission policies."""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from argus.core import cost_control as costs
from argus.core.usage import UsageLedger, UsageRecord, _rewrite_usage_rows


def reservation(root, project, call_id="observed", cap=10):
    return costs.reserve_call_budget(
        call_id=call_id, project_root=project, mission_id="mission", provider="pi",
        model="unlisted", run_label="engineer", global_root=root, global_daily_cap_usd=cap,
    )


def receipt(project, status, amount):
    return UsageRecord.from_jsonable({
        "call_id": "observed", "project_id": project.name, "provider": "pi", "model": "unlisted",
        "status": "error", "pricing_status": status, "cost_usd": amount, "run_label": "engineer",
        "started_at": time.time() - 1, "completed_at": time.time(),
    })


@pytest.mark.parametrize("policy", ["block", "allow"])
@pytest.mark.parametrize("status,partial", [("unknown", None), ("partial", None), ("unpriced", None), ("partial", 4), ("unpriced", 4), ("partial", 12)])
@pytest.mark.parametrize("settled", [6, 10, 12])
def test_floor_survives_finalization_without_double_counting(tmp_path, monkeypatch, policy, status, partial, settled):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", policy)
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    project = tmp_path / "projects" / "p1"
    call, reason = reservation(tmp_path, project)
    assert call is not None and not reason
    assert "budget exhausted" in call.observe_cost(10)
    ledger = UsageLedger(project, migrate_legacy=False)
    pending = receipt(project, status, partial)
    if status == "unknown":
        call.settle_unknown(reason="missing final receipt")
    else:
        ledger.append(pending)
        snapshot = costs.cost_control_snapshot(global_root=tmp_path)
        assert snapshot["in_flight_cost_usd"] == max(0, 10 - (partial or 0))
        call.settle(pending)
    for _ in range(2):
        snapshot = costs.cost_control_snapshot(global_root=tmp_path)
        assert snapshot["active_reservations"] == 0
        assert snapshot["in_flight_cost_usd"] == 0
        assert snapshot["unacknowledged_observed_cost_usd"] == max(0, 10 - (partial or 0))
        assert snapshot["blocking_unresolved_calls"] == (1 if policy == "block" else 0)
        assert "budget exhausted" in costs.cost_admission_reason(global_root=tmp_path, cap=max(10, partial or 0))
    above = costs.cost_admission_reason(global_root=tmp_path, cap=max(10, partial or 0) + 1)
    assert ("unresolved provider cost" in above) if policy == "block" else above == ""
    with ledger._locked():
        _rewrite_usage_rows(ledger.path, [replace(pending, pricing_status="priced", cost_usd=settled).to_jsonable()])
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == snapshot["unacknowledged_observed_cost_usd"] == 0
    assert costs.cost_admission_reason(global_root=tmp_path, cap=settled + 1) == ""
    assert "budget exhausted" in costs.cost_admission_reason(global_root=tmp_path, cap=settled)


def test_acknowledgement_during_partial_live_settlement_is_not_a_second_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    project = tmp_path / "projects" / "p1"
    call, _ = reservation(tmp_path, project, cap=100)
    assert call is not None
    call.observe_cost(10)
    pending = receipt(project, "partial", 4)
    UsageLedger(project, migrate_legacy=False).append(pending)
    with pytest.raises(ValueError, match="already known"):
        costs.acknowledge_unpriced_call(global_root=tmp_path, project_id="p1", call_id="observed", liability_usd=5, reason="test")
    costs.acknowledge_unpriced_call(global_root=tmp_path, project_id="p1", call_id="observed", liability_usd=12, reason="test")
    for closed in [False, True]:
        if closed:
            call.settle(pending)
        snapshot = costs.cost_control_snapshot(global_root=tmp_path)
        assert snapshot["pending_liability_usd"] == 8
        assert snapshot["in_flight_cost_usd"] == snapshot["unacknowledged_observed_cost_usd"] == 0
        assert costs.cost_admission_reason(global_root=tmp_path, cap=13) == ""
        assert "budget exhausted" in costs.cost_admission_reason(global_root=tmp_path, cap=12)
