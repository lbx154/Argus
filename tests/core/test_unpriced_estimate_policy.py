"""A call whose cost is not settled counts as the day's costliest priced call.

That is the default (``ARGUS_SKILL_UNPRICED_COST_POLICY=estimate``): work goes
on, the daily cap sees a figure that can only err against the campaign, and the
cockpit shows how much is being counted. ``block`` keeps refusing new calls
until the operator acknowledges the held one.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus.core import cost_control as costs
from argus.core import usage
from argus.core.pricing import copilot_usd_per_premium_request
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, UsageRecord, build_usage_record


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_UNPRICED_COST_POLICY", raising=False)
    with usage._RECENT_RECORDS_CACHE_LOCK:
        usage._RECENT_RECORDS_CACHE.clear()
    yield


def _priced(project: Path, call_id: str, cost: float, *, tier: str = "token") -> UsageRecord:
    now = time.time()
    return UsageRecord.from_jsonable({
        "call_id": call_id, "project_id": project.name, "provider": "pi", "model": "gpt-5.6-sol",
        "run_label": "engineer", "status": "completed", "pricing_status": "priced",
        "pricing_tier": tier, "cost_usd": cost, "cost_basis": "provider_reported",
        "started_at": now - 1, "completed_at": now,
    })


def _unsettled(project: Path, call_id: str, *, provider: str = "pi") -> UsageRecord:
    now = time.time()
    return UsageRecord.from_jsonable({
        "call_id": call_id, "project_id": project.name, "provider": provider,
        "model": "gpt-5.6-sol", "run_label": "engineer", "status": "error",
        "pricing_status": "partial", "pricing_tier": "unknown", "cost_usd": None,
        "started_at": now - 1, "completed_at": now,
        "error": "External interrupt: the provider never reported usage",
    })


def _ledger(root: Path, *records: UsageRecord) -> Path:
    project = root / "projects" / "research"
    UsageLedger(project, migrate_legacy=False).append_many(records)
    return project


def _reserve(root: Path, project: Path, call_id: str, *, cap: float = 1000.0, model: str = "gpt-5.6-sol"):
    return costs.reserve_call_budget(
        call_id=call_id, project_root=project, mission_id="research", provider="pi",
        model=model, run_label="engineer-r1", global_root=root, global_daily_cap_usd=cap,
    )


def test_estimate_is_the_default_and_allow_means_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    assert costs._unpriced_policy() == "estimate"
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "allow")
    assert costs._unpriced_policy() == "estimate"
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    assert costs._unpriced_policy() == "block"


def test_an_unsettled_call_counts_as_the_days_costliest_priced_call(tmp_path: Path) -> None:
    project = _ledger(tmp_path, _priced(project := tmp_path / "projects" / "research", "p1", 2.0),
                      _priced(project, "p2", 5.0), _unsettled(project, "open"))

    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["policy"] == "estimate"
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    assert snapshot["unpriced_estimate_usd"] == 5.0
    assert snapshot["counted_unpriced_usd"] == 5.0
    assert snapshot["unacknowledged_observed_cost_usd"] == 0
    # Settled $7 plus the $5 the open call counts for.
    assert costs.cost_admission_reason(global_root=tmp_path, cap=12.0).startswith(
        "global daily budget exhausted"
    )
    assert costs.cost_admission_reason(global_root=tmp_path, cap=12.5) == ""
    admitted, reason = _reserve(tmp_path, project, "next", cap=12.5)
    assert admitted is not None and reason == ""
    admitted.release(reason="test")


def test_observed_spend_above_the_estimate_is_what_counts(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "research"
    _ledger(tmp_path, _priced(project, "p1", 5.0))
    running, reason = _reserve(tmp_path, project, "open")
    assert running is not None and reason == ""
    assert running.observe_cost(30.0) == ""
    running.settle_unknown(reason="no final receipt")

    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["counted_unpriced_usd"] == 30.0
    assert snapshot["unacknowledged_observed_cost_usd"] == 30.0
    assert costs.cost_admission_reason(global_root=tmp_path, cap=35.0).startswith(
        "global daily budget exhausted"
    )
    assert costs.cost_admission_reason(global_root=tmp_path, cap=35.5) == ""


def test_without_a_priced_call_only_copilot_counts_one_premium_request(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "research"
    _ledger(tmp_path, _unsettled(project, "pi-open"))
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["unpriced_estimate_usd"] == 0.0
    assert snapshot["counted_unpriced_usd"] == 0.0

    other = tmp_path / "projects" / "copilot"
    UsageLedger(other, migrate_legacy=False).append(_unsettled(other, "copilot-open", provider="copilot"))
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 2
    assert snapshot["counted_unpriced_usd"] == pytest.approx(copilot_usd_per_premium_request())


def test_a_model_without_a_price_is_counted_rather_than_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "research"
    _ledger(tmp_path, _priced(project, "p1", 3.0))
    unpriced = build_usage_record(
        call_id="future", project_root=project, mission_id="research", provider="pi",
        model="future-model", run_label="engineer", started_at=time.time() - 1,
        completed_at=time.time(), status="completed",
        token_usage=TokenUsage(input_tokens=1000, output_tokens=100,
                               input_tokens_present=True, output_tokens_present=True, source="test"),
    )
    assert unpriced.pricing_status == "unpriced"
    UsageLedger(project, migrate_legacy=False).append(unpriced)

    admitted, reason = _reserve(tmp_path, project, "again", model="future-model")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["counted_unpriced_usd"] == 3.0
    assert snapshot["unresolved"][0]["missing_price"] is True

    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    refused, reason = _reserve(tmp_path, project, "refused", model="future-model")
    assert refused is None and reason.startswith("unpriced model")


def test_an_acknowledgement_replaces_the_estimate_with_the_operators_figure(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "research"
    _ledger(tmp_path, _priced(project, "p1", 5.0), _unsettled(project, "open"))
    assert costs.cost_admission_reason(global_root=tmp_path, cap=10.0).startswith(
        "global daily budget exhausted"
    )

    costs.acknowledge_unpriced_call(
        global_root=tmp_path, project_id=project.name, call_id="open",
        liability_usd=1.0, reason="the provider's dashboard shows one dollar",
    )

    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["pending_liability_usd"] == 1.0
    assert snapshot["counted_unpriced_usd"] == 0.0
    assert costs.cost_admission_reason(global_root=tmp_path, cap=6.5) == ""


def test_block_still_holds_an_unsettled_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    project = tmp_path / "projects" / "research"
    _ledger(tmp_path, _priced(project, "p1", 5.0), _unsettled(project, "open"))

    refused, reason = _reserve(tmp_path, project, "next")
    assert refused is None and reason.startswith("unresolved provider cost")
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["policy"] == "block"
    assert snapshot["blocking_unresolved_calls"] == 1
    assert snapshot["unpriced_estimate_usd"] == 0.0
    assert snapshot["counted_unpriced_usd"] == 0.0


def test_journal_repair_estimates_do_not_set_the_days_estimate(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "research"
    _ledger(
        tmp_path,
        _priced(project, "repair", 50.0, tier=costs.JOURNAL_REPAIR_TIER),
        _priced(project, "p1", 2.0),
        _unsettled(project, "open"),
    )
    snapshot = costs.cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unpriced_estimate_usd"] == 2.0
    assert snapshot["counted_unpriced_usd"] == 2.0
