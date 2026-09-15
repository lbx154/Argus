from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend._budget_monitor import LiveBudgetMonitor
from argus.core.cost_control import (
    cost_admission_reason,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.knobs import normalize_cockpit_knob_value
from argus.core.stop_kinds import stop_kind_from_external_interrupt
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, build_usage_record


@pytest.fixture
def budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "on")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "1000")
    return tmp_path


def reserve(root, name):
    return reserve_call_budget(call_id=name, project_root=root / "projects" / name,
        mission_id="mission", provider="pi", model="gpt-5.6-sol", run_label="engineer-r1", global_root=root)


def test_concurrent_zero_dollar_calls_hit_token_limit_and_recover_on_change(budget, monkeypatch):
    a, _ = reserve(budget, "a")
    b, _ = reserve(budget, "b")
    assert a is not None and b is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        reasons = list(pool.map(lambda pair: pair[0].observe_cost(0, tokens=pair[1]), [(a, 600), (b, 400)]))
    assert sum("token budget exhausted" in reason for reason in reasons) == 1
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 1000
    assert reserve(budget, "c")[0] is None
    assert stop_kind_from_external_interrupt(cost_admission_reason(global_root=budget)) == "budget_exhausted"
    # A lower repeated observation cannot refund usage or trigger auto-resume.
    assert "token budget exhausted" in a.observe_cost(tokens=100)
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "2000")
    assert cost_admission_reason(global_root=budget) == ""
    assert reserve(budget, "c")[0] is not None


def test_settlement_counts_cache_once_and_does_not_double_count_live_receipt(budget):
    call, _ = reserve(budget, "settled")
    assert call is not None
    call.observe_cost(tokens=650)
    record = build_usage_record(call_id="settled", project_root=call.project_root,
        mission_id="mission", provider="pi", model="gpt-5.6-sol", run_label="engineer",
        started_at=time.time() - 1, completed_at=time.time(), status="completed",
        provider_cost_usd=0, token_usage=TokenUsage(input_tokens=600, cached_input_tokens=500,
            output_tokens=40, reasoning_output_tokens=10, input_tokens_present=True,
            output_tokens_present=True, reasoning_output_tokens_present=True))
    UsageLedger(call.project_root, migrate_legacy=False).append(record)
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 650
    call.settle(record)
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 650


def test_unknown_final_receipt_preserves_observed_tokens(budget):
    call, _ = reserve(budget, "lost")
    assert call is not None
    call.observe_cost(tokens=1000)
    call.settle_unknown(reason="lost final receipt")
    assert "token budget exhausted" in cost_admission_reason(global_root=budget)
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 1000


def test_daily_token_allowance_resets_at_midnight(budget):
    call, _ = reserve(budget, "overnight")
    assert call is not None
    from argus.core.cost_control import _local_day_start
    next_day = _local_day_start(time.time()) + 36 * 3600
    call.observe_cost(tokens=1000)
    assert cost_admission_reason(global_root=budget, now=next_day) == ""
    assert call.observe_cost(tokens=10, now=next_day) == ""
    assert cost_control_snapshot(global_root=budget, now=next_day)["daily_tokens"] == 10


@pytest.mark.parametrize("value", ["-1", "1.5", "nan", "inf", "1e6", "junk"])
def test_token_limit_requires_nonnegative_integer(value):
    with pytest.raises(ValueError):
        normalize_cockpit_knob_value("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", value)


def test_pi_monitor_ignores_repeated_summaries_and_interrupts_before_agent_end(budget):
    import json

    call, _ = reserve(budget, "pi-live")
    assert call is not None
    ctx = SimpleNamespace(backend=SimpleNamespace(_backend_name="pi", _is_copilot=False),
                          cost_reservation=call, resume_thread_id=None)
    monitor = LiveBudgetMonitor(ctx, interval_seconds=60)
    assert monitor.check() is None
    message = {"role": "assistant", "usage": {"input": 100, "cacheRead": 700,
        "output": 200, "cost": {"total": 0}}, "stopReason": "toolUse"}
    monitor.observe("engineer.stdout", json.dumps({"type": "turn_end", "message": message}))
    monitor.observe("engineer.stderr", json.dumps({"type": "message_end", "message": message}))
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 0
    monitor.observe("engineer.stdout", json.dumps({"type": "message_end", "message": message}))
    assert "token budget exhausted" in monitor.check()
    assert cost_control_snapshot(global_root=budget)["daily_tokens"] == 1000


def test_pi_monitor_publishes_dollars_with_missing_final_agent_receipt(budget, monkeypatch):
    import json

    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "0")
    call, _ = reserve(budget, "pi-priced")
    assert call is not None
    monitor = LiveBudgetMonitor(SimpleNamespace(backend=SimpleNamespace(_backend_name="pi", _is_copilot=False),
        cost_reservation=call, resume_thread_id=None), interval_seconds=0)
    for cost in (6, 4):
        monitor.observe("stdout", json.dumps({"type": "message_end", "message": {
            "role": "assistant", "usage": {"input": 10, "output": 3, "cost": {"total": cost}}}}))
    assert "global daily budget exhausted" in monitor.check()
    assert cost_control_snapshot(global_root=budget)["in_flight_cost_usd"] == 10


def test_denied_receipt_cannot_spend_tokens(budget):
    record = build_usage_record(call_id="denied", project_root=budget / "projects" / "denied",
        mission_id=None, provider="pi", model="gpt-5.6-sol", run_label="manager",
        started_at=time.time(), completed_at=time.time(), status="denied")
    UsageLedger(budget / "projects" / "denied", migrate_legacy=False).append(replace(record, input_tokens=10000))
    assert cost_admission_reason(global_root=budget) == ""
