from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from argus.core import cost_control as costs
from argus.core.usage import UsageLedger, UsageRecord
from argus.life.supervisor import LifeBudget


@pytest.fixture(autouse=True)
def budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "100")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")


def record(project, call_id, cost=None):
    return UsageRecord.from_jsonable({
        "call_id": call_id, "project_id": project.name, "provider": "pi",
        "model": "test-model", "run_label": "map-summary", "status": "error",
        "pricing_status": "unpriced" if cost is None else "priced", "cost_usd": cost,
        "started_at": time.time() - 1, "completed_at": time.time(),
        "error": "External interrupt: Map text generation timed out",
    })


def reserve(root, project, call_id):
    return costs.reserve_call_budget(
        call_id=call_id, project_root=project, mission_id="research",
        provider="pi", model="test-model", run_label="engineer-r1", global_root=root,
    )


def acknowledge(root, project, call_id="map-timeout", amount=5.0, reason="Operator accepts this bounded risk"):
    return costs.acknowledge_unpriced_call(
        global_root=root, project_id=project.name, call_id=call_id,
        liability_usd=amount, reason=reason,
    )


def test_map_unknown_does_not_interrupt_an_already_admitted_research_call(tmp_path):
    project = tmp_path / "projects" / "research"
    running, _ = reserve(tmp_path, project, "running")
    assert running is not None
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(record(project, "map-timeout"))

    assert running.observe_cost(10) == ""
    denied, reason = reserve(tmp_path, project, "next")
    assert denied is None and reason.startswith("unresolved provider cost")
    # Isolation is not a dollar-cap bypass, even when another cost is unknown.
    assert "global daily budget exhausted" in running.observe_cost(100)
    assert costs.cost_control_snapshot(global_root=tmp_path)["blocking_unresolved_calls"] == 1


def test_acknowledgement_preserves_unknown_ledger_and_budgets_one_call_only(tmp_path):
    project = tmp_path / "projects" / "research"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(record(project, "map-timeout"))
    before = ledger.path.read_bytes()
    decision = acknowledge(tmp_path, project)

    assert ledger.path.read_bytes() == before
    assert decision["liability_usd"] == 5
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == snapshot["acknowledged_unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    assert snapshot["pending_liability_usd"] == 5
    assert snapshot["unresolved"][0]["pricing_status"] == "unpriced"
    assert snapshot["unresolved"][0]["blocking"] is False
    assert snapshot["policy"] == "block"
    admitted, reason = reserve(tmp_path, project, "next")
    assert admitted is not None and reason == ""
    admitted.release()

    ledger.append(record(project, "another-unknown"))
    denied, reason = reserve(tmp_path, project, "after-another-failure")
    assert denied is None and "another-unknown" in reason


def test_risk_liability_counts_toward_cap_and_reconciliation_replaces_it(tmp_path):
    project = tmp_path / "projects" / "research"
    ledger = UsageLedger(project, migrate_legacy=False)
    unknown = record(project, "map-timeout")
    ledger.append(unknown)
    ledger.append(record(project, "earlier", cost=96))
    acknowledge(tmp_path, project)
    assert "global daily budget exhausted" in costs.cost_admission_reason(global_root=tmp_path)

    # Simulate an authoritative, late provider settlement under the usage lock.
    with ledger._locked():
        rows = [replace(unknown, cost_usd=1, pricing_status="priced").to_jsonable(),
                record(project, "earlier", cost=96).to_jsonable()]
        ledger.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == snapshot["pending_liability_usd"] == 0
    assert costs.cost_admission_reason(global_root=tmp_path) == ""


def test_partial_cost_is_not_counted_twice_in_liability(tmp_path):
    project = tmp_path / "projects" / "research"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(replace(record(project, "map-timeout", cost=3), pricing_status="partial"))
    acknowledge(tmp_path, project, amount=5)
    assert costs.cost_control_snapshot(global_root=tmp_path)["pending_liability_usd"] == 2
    assert costs.cost_admission_reason(global_root=tmp_path, cap=5).startswith("global daily budget exhausted")
    assert costs.cost_admission_reason(global_root=tmp_path, cap=6) == ""


@pytest.mark.parametrize("amount", [0, -1, float("inf"), float("nan"), True, "5"])
def test_invalid_liability_never_bypasses_admission(tmp_path, amount):
    project = tmp_path / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append(record(project, "map-timeout"))
    with pytest.raises(ValueError, match="finite and positive"):
        acknowledge(tmp_path, project, amount=amount)
    assert costs.cost_admission_reason(global_root=tmp_path).startswith("unresolved provider cost")


def test_acknowledgement_is_project_scoped_and_concurrently_idempotent(tmp_path):
    project = tmp_path / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append(record(project, "map-timeout"))
    with pytest.raises(LookupError):
        acknowledge(tmp_path, tmp_path / "projects" / "other")
    with pytest.raises(ValueError):
        acknowledge(tmp_path, project, reason="   ")
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda _: acknowledge(tmp_path, project), range(2)))
    assert decisions[0] == decisions[1]
    with pytest.raises(ValueError, match="different acknowledgement"):
        acknowledge(tmp_path, project, amount=9)
    audit = [json.loads(line) for line in (tmp_path / costs.COST_CONTROL_AUDIT_FILE).read_text().splitlines()]
    assert len([r for r in audit if r["type"] == "budget.unpriced.acknowledged"]) == 1


def test_supervisor_preflight_waits_for_reconciliation_instead_of_retrying_denied_calls(tmp_path):
    project = tmp_path / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append(record(project, "map-timeout"))
    budget = LifeBudget(global_daily_cap_usd=100)
    allowed, reason = budget.can_start(global_root=tmp_path)
    assert not allowed and reason.startswith("unresolved provider cost")
    acknowledge(tmp_path, project)
    assert budget.can_start(global_root=tmp_path) == (True, "")


def test_missing_final_receipt_retains_observed_incurred_cost(tmp_path):
    project = tmp_path / "projects" / "research"
    running, _ = reserve(tmp_path, project, "map-timeout")
    assert running is not None and running.observe_cost(30) == ""
    ledger = UsageLedger(project, migrate_legacy=False)
    unknown = record(project, "map-timeout")
    ledger.append(unknown)
    running.settle(unknown)
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["in_flight_cost_usd"] == 0
    assert snapshot["unacknowledged_observed_cost_usd"] == 30
    with pytest.raises(ValueError, match="already known"):
        acknowledge(tmp_path, project, amount=5)
    acknowledge(tmp_path, project, amount=35)
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unacknowledged_observed_cost_usd"] == 0
    assert snapshot["pending_liability_usd"] == 35
    assert costs.cost_admission_reason(global_root=tmp_path, cap=35).startswith("global daily budget exhausted")
    assert ledger.records()[0].cost_usd is None


def test_observed_lower_bound_is_enforced_even_with_allow_policy(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "research"
    running, _ = reserve(tmp_path, project, "map-timeout")
    assert running is not None and running.observe_cost(30) == ""
    running.settle_unknown(reason="No final receipt")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "allow")
    assert costs.cost_admission_reason(global_root=tmp_path, cap=30).startswith("global daily budget exhausted")


def test_legacy_state_migrates_without_losing_unknown_costs(tmp_path):
    project = tmp_path / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append(record(project, "map-timeout"))
    state = costs._default_state(time.time())
    state["version"] = 1
    state.pop("acknowledgements")
    path = tmp_path / costs.COST_CONTROL_STATE_FILE
    path.write_text(json.dumps(state), encoding="utf-8")
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["blocking_unresolved_calls"] == 1
    migrated = json.loads(path.read_text())
    assert migrated["version"] == 2 and migrated["acknowledgements"] == {}


def test_unknown_future_state_version_is_not_silently_rewritten(tmp_path):
    state = costs._default_state(time.time())
    state["version"] = 999
    path = tmp_path / costs.COST_CONTROL_STATE_FILE
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(costs.CostControlStateError, match="unsupported"):
        costs.cost_control_snapshot(global_root=tmp_path)
    assert path.read_bytes() == before


def test_corrupt_acknowledgement_fails_closed(tmp_path):
    project = tmp_path / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append(record(project, "map-timeout"))
    acknowledge(tmp_path, project)
    state_path = tmp_path / costs.COST_CONTROL_STATE_FILE
    state = json.loads(state_path.read_text())
    state["acknowledgements"]["map-timeout"]["liability_usd"] = -1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(costs.CostControlStateError):
        costs.cost_admission_reason(global_root=tmp_path)
