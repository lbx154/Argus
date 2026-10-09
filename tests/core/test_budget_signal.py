from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from argus.core.budget_signal import (
    MissionBudget,
    budget_signal,
    mission_budget,
    mission_budget_reached,
    mission_usage_summary,
    record_mission_budget_pause,
    take_mission_budget_pause,
    usage_by_mission,
)
from argus.core.usage import UsageLedger, UsageRecord
from argus.provider_integrations.account_budget import maybe_warn_low_account_quota
from argus.provider_integrations.copilot_account_quota import AccountQuota


def _record(call_id: str, mission_id: str, *, requests: float = 1.0, usd: float = 0.04,
            nano_aiu: int | None = None) -> UsageRecord:
    now = time.time()
    return UsageRecord(
        call_id=call_id, project_id="p", mission_id=mission_id, provider="copilot",
        model="m", run_label="engineer", started_at=now - 1, completed_at=now,
        status="succeeded", input_tokens=100, cached_input_tokens=0, output_tokens=10,
        reasoning_output_tokens=0, premium_requests=requests, pricing_status="priced",
        pricing_tier="t", cost_usd=usd, cost_basis="provider", total_nano_aiu=nano_aiu,
    )


def _quota(mode: str, *, remaining: float, entitlement: float, percent: float, error: str = "") -> AccountQuota:
    return AccountQuota(
        provider="copilot", login="someone", plan="p", billing_mode=mode,  # type: ignore[arg-type]
        entitlement=entitlement, remaining=remaining, used=entitlement - remaining,
        percent_remaining=percent, reset_date="2026-11-01", overage_permitted=False,
        unlimited=False, fetched_at=time.time(), error=error,
    )


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MISSION_BUDGET_USD", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_ACCOUNT_QUOTA_WARN_PERCENT", raising=False)


def test_request_billed_signal_names_the_count_and_suggests_fewer_calls() -> None:
    text = budget_signal(_quota("request", remaining=37, entitlement=300, percent=12.3))
    assert "request-billed" in text
    assert "37 of 300 left this month" in text
    assert "fewer, larger calls" in text
    assert "not a limit" in text
    assert "LOW" not in text


def test_credit_billed_signal_names_the_share_and_suggests_short_contexts() -> None:
    text = budget_signal(_quota("credit", remaining=576_234, entitlement=1_000_000, percent=57.6))
    assert "credit-billed" in text
    assert "58% of the monthly credits left" in text
    assert "shorter contexts" in text


def test_low_quota_is_flagged_in_the_signal() -> None:
    text = budget_signal(_quota("request", remaining=5, entitlement=300, percent=1.7))
    assert "LOW: under 10%" in text


def test_unreadable_quota_does_not_advise_a_mode() -> None:
    text = budget_signal(_quota("unknown", remaining=0, entitlement=0, percent=0, error="URLError"))
    assert "could not be read" in text
    assert "fewer, larger" not in text and "shorter contexts" not in text


def test_no_quota_and_no_spend_gives_no_signal() -> None:
    assert budget_signal(None) == ""


def test_mission_spend_sums_every_attempt_of_one_item(tmp_path: Path) -> None:
    UsageLedger(tmp_path, migrate_legacy=False).append_many([
        _record("a", "item1:attempt:1", requests=2),
        _record("b", "item1:attempt:2", requests=3, nano_aiu=2_500_000_000),
        _record("c", "item10:attempt:1", requests=7),
        _record("d", "item2:attempt:1", requests=11),
    ])
    summary = mission_usage_summary(tmp_path, "item1")
    assert summary.call_count == 2
    assert summary.premium_requests == pytest.approx(5)
    rows = usage_by_mission(UsageLedger(tmp_path, migrate_legacy=False).records())
    assert rows["item1"]["premium_requests"] == pytest.approx(5)
    assert rows["item1"]["credits"] == pytest.approx(2.5)
    assert rows["item10"]["calls"] == 1
    text = budget_signal(None, mission=summary)
    assert "this mission has spent 5 premium requests, 2.5 credits" in text


def test_mission_budget_is_off_by_default_and_reached_only_at_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    assert mission_budget().enabled is False
    UsageLedger(tmp_path, migrate_legacy=False).append_many(
        [_record(f"c{i}", "item1:attempt:1", requests=1, usd=0.5) for i in range(4)]
    )
    summary = mission_usage_summary(tmp_path, "item1")
    assert mission_budget_reached(summary, MissionBudget()) == ""
    assert mission_budget_reached(summary, MissionBudget(requests=5)) == ""
    assert "4 of 4 premium requests" in mission_budget_reached(summary, MissionBudget(requests=4))
    assert "$2.00 of $1.50" in mission_budget_reached(summary, MissionBudget(usd=1.5))
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "40")
    assert mission_budget() == MissionBudget(requests=40.0)


def test_pause_marker_is_consumed_once(tmp_path: Path) -> None:
    summary = mission_usage_summary(tmp_path, "nothing")
    record_mission_budget_pause(tmp_path, "item1", reached="x", summary=summary, budget=MissionBudget(requests=1))
    marker = take_mission_budget_pause(tmp_path, "item1")
    assert marker is not None and marker["reached"] == "x"
    assert take_mission_budget_pause(tmp_path, "item1") is None


def test_low_quota_warning_is_emitted_once_per_account_period(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    low = _quota("credit", remaining=50_000, entitlement=1_000_000, percent=5.0)
    assert maybe_warn_low_account_quota(tmp_path, events.append, quota=low) is True
    assert maybe_warn_low_account_quota(tmp_path, events.append, quota=low) is False
    assert len(events) == 1
    assert events[0]["operator_alert"] is True
    assert "5%" in events[0]["text"] and "keeps working" in events[0]["text"]
    healthy = _quota("credit", remaining=900_000, entitlement=1_000_000, percent=90.0)
    assert maybe_warn_low_account_quota(tmp_path / "other", events.append, quota=healthy) is False
    zh: list[dict[str, Any]] = []
    assert maybe_warn_low_account_quota(tmp_path / "zh", zh.append, quota=low, chinese=True)
    assert "额度" in zh[0]["text"]


def test_manager_supervision_prompt_carries_the_budget_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from argus.manager import supervision
    from argus.manager.observation import ManagerObservation

    observation = ManagerObservation(
        root=tmp_path, continuous=None, control_revision="c", evidence_revision="e",  # type: ignore[arg-type]
        facts={"objective": "x"},
    )
    monkeypatch.setattr("argus.provider_integrations.account_budget.role_budget_signal", lambda **_kw: "")
    plain = supervision._prompt(observation)
    assert "Account budget" not in plain
    monkeypatch.setattr(
        "argus.provider_integrations.account_budget.role_budget_signal",
        lambda **_kw: "Account budget (information for your judgement, not a limit on what to do): x.",
    )
    assert "Account budget (information" in supervision._prompt(observation)


def test_role_signal_is_empty_for_non_metered_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from argus.provider_integrations.account_budget import role_budget_signal

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    assert role_budget_signal(role="planner") == ""
