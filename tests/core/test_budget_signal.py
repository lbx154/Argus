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


def _no_advice(text: str) -> None:
    lowered = text.lower()
    for phrase in ("prefer", "fewer", "larger calls", "shorter context", "fold", "excerpt", "warned"):
        assert phrase not in lowered
    assert text.endswith("Spend never reduces review, verification, or acceptance checks.")


def test_request_billed_signal_states_facts_only() -> None:
    text = budget_signal(_quota("request", remaining=37, entitlement=300, percent=12.3))
    assert "request-billed" in text
    assert "multiplier" in text
    assert "37 of 300 premium requests left this month, resets 2026-11-01" in text
    assert "warning threshold" not in text
    _no_advice(text)


def test_credit_billed_signal_states_facts_only() -> None:
    text = budget_signal(_quota("credit", remaining=576_234, entitlement=1_000_000, percent=57.6))
    assert "credit-billed" in text
    assert "58% of the monthly credits left" in text
    _no_advice(text)


def test_low_quota_is_reported_as_a_quota_threshold() -> None:
    text = budget_signal(_quota("request", remaining=5, entitlement=300, percent=1.7))
    assert "remaining quota is below the operator's 10% warning threshold" in text
    assert "of the month" not in text
    _no_advice(text)


def test_operator_budget_is_reported_when_set() -> None:
    text = budget_signal(None, budget=MissionBudget(requests=40))
    assert "operator per-mission budget: 40 premium requests" in text
    assert "the operator decides" in text
    _no_advice(text)


def test_unreadable_quota_reports_unknown_mode() -> None:
    text = budget_signal(_quota("unknown", remaining=0, entitlement=0, percent=0, error="URLError"))
    assert "could not be read" in text
    _no_advice(text)


def test_no_quota_and_no_budget_gives_no_signal() -> None:
    assert budget_signal(None) == ""
    assert budget_signal(None, budget=MissionBudget()) == ""


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
        lambda **_kw: "Account budget (facts only): x.",
    )
    assert "Account budget (facts only): x." in supervision._prompt(observation)


def test_role_signal_is_empty_for_non_metered_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from argus.provider_integrations.account_budget import role_budget_signal

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    assert role_budget_signal(role="planner") == ""


def test_role_signal_never_waits_on_the_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The prompt path returns at once and leaves the probe to a background thread."""
    from argus.provider_integrations import copilot_account_quota as quota_mod
    from argus.provider_integrations.account_budget import role_budget_signal

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TOKEN_FROM_ENV", "1")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "test-token")

    def never(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("the prompt path must not call the provider")

    started: list[object] = []

    class _Thread:
        def __init__(self, target: Any, name: str, daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            started.append(self.target)

    monkeypatch.setattr(quota_mod, "fetch_copilot_user", never)
    monkeypatch.setattr(quota_mod.threading, "Thread", _Thread)
    began = time.monotonic()
    assert role_budget_signal(role="manager") == ""
    assert time.monotonic() - began < 1.0
    assert len(started) == 1
