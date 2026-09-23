"""Real TS -> Python budget owner -> offline Pi subprocess, with durable ledgers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from argus.core.cost_control import cost_admission_reason, cost_control_snapshot
from argus.core.usage import UsageLedger, UsageRecord

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "packages/runtime/fixtures/budgeted-run.mjs"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not shutil.which("node") or not (ROOT / "packages/runtime/dist/budgetedPi.js").is_file(),
    reason="build Node packages for budgeted Pi integration",
)]


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "projects/p").mkdir(parents=True)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "0")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    return tmp_path


def run(root, mode="accounting-tokens", *, cancel=False, request=None):
    result = subprocess.run([shutil.which("node"), str(ENTRY)], input=json.dumps({
        "python": sys.executable, "sourceRoot": str(ROOT), "globalRoot": str(root), "mode": mode, "cancel": cancel,
        "request": request or {},
    }), capture_output=True, text=True, encoding="utf-8", env=os.environ.copy(), timeout=30, check=True)
    return json.loads(result.stdout)


def test_budgeted_node_call_settles_native_per_turn_price(root):
    result = run(root)
    assert result["admitted"] and result["settlement"] == "settled", json.dumps(result)
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.call_id == result["callId"]
    assert row.cost_usd == pytest.approx(1.506) and row.input_tokens == 300_000
    assert row.cost_basis == "typescript_pi" and row.pricing_status == "priced"
    assert cost_control_snapshot(global_root=root)["active_reservations"] == 0
    assert cost_admission_reason(global_root=root) == ""


def test_exhausted_budget_never_starts_pi(root):
    import time

    UsageLedger(root / "projects/p", migrate_legacy=False).append(UsageRecord.from_jsonable({
        "call_id": "already-spent", "project_id": "p", "completed_at": time.time(),
        "cost_usd": 10, "pricing_status": "priced",
    }))
    result = run(root)
    assert result["admitted"] is False and result["events"] == 0
    assert result["runner"] is None and result["settlement"] == "not_started"
    assert len(UsageLedger(root / "projects/p", migrate_legacy=False).records()) == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_cap_interrupt_or_operator_cancellation_keeps_partial_cost(root, monkeypatch, cancel):
    if not cancel:
        monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0.5")
    result = run(root, "accounting-cancel", cancel=cancel)
    assert result["settlement"] == "unresolved", result
    assert not result["runner"]["turnCompleted"]
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == pytest.approx(0.753)
    assert row.pricing_status == "partial"
    assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1
    assert run(root)["admitted"] is False


def test_missing_usage_never_settles_at_zero(root):
    result = run(root, "success")
    assert result["runner"]["turnCompleted"], json.dumps(result)
    assert result["settlement"] == "unresolved"
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd is None and row.pricing_status == "partial"
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)


def test_invalid_late_usage_preserves_the_earlier_durable_lower_bound(root):
    result = run(root, "accounting-unsafe")
    assert result["settlement"] == "unresolved"
    assert "safe-integer" in result["reason"]
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == pytest.approx(0.753) and row.pricing_status == "partial"


def test_provider_turn_allowance_retains_native_cost_and_unresolved_liability(root):
    result = run(root, "turn-cap-paced", request={"providerTurnCap": 2})
    assert result["settlement"] == "unresolved"
    assert result["runner"]["providerTurnCapHit"]
    assert result["runner"]["stopKind"] == "provider_turn_limit"
    assert result["runner"]["agentMessages"][:2] == ["checkpoint 1", "checkpoint 2"]
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == pytest.approx(result["runner"]["providerTurns"] * 0.1)
    assert row.cost_usd >= 0.2 and row.pricing_status == "partial"
    assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1


def test_guarded_structured_call_settles_without_persisting_schema_in_cost_files(root):
    schema = {"type": "object", "description": "private-schema-content-native-fixture"}
    result = run(root, "structured-completions", request={"outputSchema": schema})
    assert result["settlement"] == "settled"
    assert result["runner"]["agentMessages"] == ['{"answer":"fixture"}']
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == pytest.approx(0.1)
    assert schema["description"] not in json.dumps(row.to_jsonable())


def test_rejected_schema_provider_leaves_a_native_unresolved_receipt(root):
    result = run(root, "structured-unsupported", request={"outputSchema": {"type": "object"}})
    assert result["settlement"] == "unresolved"
    assert not result["runner"]["turnCompleted"]
    assert cost_control_snapshot(global_root=root)["blocking_unresolved_calls"] == 1
