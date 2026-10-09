"""Observed lower bounds must survive partial receipts until the call settles."""
from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from argus.core import cost_control as costs
from argus.core.usage import UsageLedger, UsageRecord


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


@pytest.mark.parametrize("status,partial", [("unknown", None), ("partial", None), ("unpriced", None), ("partial", 4), ("unpriced", 4), ("partial", 12)])
@pytest.mark.parametrize("settled", [6, 10, 12])
def test_floor_survives_finalization_without_double_counting(tmp_path, monkeypatch, status, partial, settled):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
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
        assert snapshot["observed_unpriced_usd"] == max(0, 10 - (partial or 0))
        assert "budget exhausted" in costs.cost_admission_reason(global_root=tmp_path, cap=max(10, partial or 0))
    assert costs.cost_admission_reason(global_root=tmp_path, cap=max(10, partial or 0) + 1) == ""
    with ledger._locked():
        ledger.path.write_text(json.dumps(replace(pending, pricing_status="priced", cost_usd=settled).to_jsonable()) + "\n", encoding="utf-8")
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == snapshot["observed_unpriced_usd"] == 0
    assert costs.cost_admission_reason(global_root=tmp_path, cap=settled + 1) == ""
    assert "budget exhausted" in costs.cost_admission_reason(global_root=tmp_path, cap=settled)
