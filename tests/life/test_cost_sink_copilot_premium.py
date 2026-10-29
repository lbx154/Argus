"""Copilot premium-request deltas and their contribution to mission cost."""
from __future__ import annotations

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.usage import UsageLedger, build_usage_record
from argus.life.supervisor._cost import _CostTrackingSink


class _Down:
    def handle_event(self, e: dict) -> None: ...
    def handle_stream_line(self, s: str, line: str) -> None: ...
    def close(self) -> None: ...


# --- extractor: picks the LAST (cumulative) premiumRequests -------------------


# --- adapter: de-cumulates the running total into per-call deltas -------------

def test_premium_delta_per_thread_decumulates() -> None:
    be = AgentCliBackend(backend="copilot")
    assert be._usage.premium_delta_for_thread(thread_id="t1", raw_total=7.5) == 7.5
    assert be._usage.premium_delta_for_thread(thread_id="t1", raw_total=15.0) == 7.5
    assert be._usage.premium_delta_for_thread(thread_id="t1", raw_total=15.0) == 0.0  # no new spend
    # no thread id → cannot de-cumulate; charge the raw total once
    assert be._usage.premium_delta_for_thread(thread_id=None, raw_total=3.0) == 3.0
    # counter reset (new session reuses id) → charge current total, never negative
    assert be._usage.premium_delta_for_thread(thread_id="t2", raw_total=5.0) == 5.0
    assert be._usage.premium_delta_for_thread(thread_id="t2", raw_total=2.0) == 2.0
    # zero/absent premium → 0.0 (codex/claude)
    assert be._usage.premium_delta_for_thread(thread_id="t3", raw_total=0.0) == 0.0


# --- sink: premium-request calls count as copilot cost ------------------------


def test_sink_counts_ledger_premium_requests_as_copilot_cost(tmp_path) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    for call_id, label in (("util", "manager.route"), ("distiller", "scientist.distiller")):
        ledger.append(build_usage_record(
            call_id=call_id, project_root=tmp_path, mission_id="mission",
            provider="copilot", model="gpt-5.5", run_label=label,
            started_at=1.0, completed_at=2.0, status="completed",
            premium_requests=7.5,
        ))
    sink = _CostTrackingSink(
        _Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5",
        usage_ledger=ledger, mission_id="mission",
    )

    summary, details = sink.completion_usage()
    assert summary.premium_requests == 15.0     # manager util + distiller
    assert details["copilot_cost_usd"] == pytest.approx(15.0 * 0.04)
    assert sink.total_usd() == pytest.approx(details["copilot_cost_usd"])


def test_sink_without_a_ledger_reports_no_usage() -> None:
    sink = _CostTrackingSink(_Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5")
    sink.handle_event({
        "type": "engineer.skill_maintenance.completed",
        "input_tokens": 120, "output_tokens": 30, "premium_requests": 7.5,
    })

    summary, details = sink.completion_usage()
    assert summary.call_count == 0 and summary.input_tokens == 0
    assert sink.total_usd() == 0
    assert details["copilot_cost_usd"] == details["engineer_cost_usd"] == 0.0


def test_copilot_rate_is_configurable(monkeypatch) -> None:
    from argus.core.pricing import copilot_usd_per_premium_request

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.10")
    assert copilot_usd_per_premium_request() == 0.10
    # bad / negative input → fail-soft to the published default
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "nope")
    assert copilot_usd_per_premium_request() == 0.04
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "-1")
    assert copilot_usd_per_premium_request() == 0.04
