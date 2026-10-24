"""Copilot premium-request deltas and their contribution to mission cost."""
from __future__ import annotations

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.life.supervisor._cost import _CostTrackingSink


class _Down:
    def handle_event(self, e: dict) -> None: ...
    def handle_stream_line(self, s: str, line: str) -> None: ...
    def close(self) -> None: ...


def _sink() -> _CostTrackingSink:
    return _CostTrackingSink(_Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5")


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


# --- sink: folds premium deltas into copilot_usd() and total_usd() -----------


def test_sink_folds_manager_util_and_scientist_premium() -> None:
    # A live copilot run also bills premium on the Manager's util turns
    # (codex.util.completed) and the scientist distiller (skill.cost.completed);
    # both must fold into the SAME meter, not just engineer/reviewer.
    sink = _sink()
    sink.handle_event({
        "type": "codex.util.completed", "model": "gpt-5.5",
        "premium_requests": 7.5, "usage_scope": "delta",
    })
    sink.handle_event({
        "type": "skill.cost.completed", "agent_layer": "scientist",
        "premium_requests": 7.5, "usage_scope": "delta",
    })
    assert sink.copilot_premium_requests == 15.0     # manager util + distiller
    assert sink.completion_usage()[1]["copilot_cost_usd"] == 15.0 * 0.04
    assert sink.total_usd() >= sink.completion_usage()[1]["copilot_cost_usd"]


def test_sink_counts_same_session_skill_maintenance_as_engineer_cost() -> None:
    sink = _sink()
    sink.handle_event({
        "type": "engineer.skill_maintenance.completed",
        "input_tokens": 120,
        "cached_input_tokens": 20,
        "output_tokens": 30,
        "reasoning_output_tokens": 10,
        "premium_requests": 7.5,
        "usage_scope": "delta",
    })

    assert sink.engineer_input_tokens == 120
    assert sink.engineer_cached_input_tokens == 20
    assert sink.engineer_output_tokens == 30
    assert sink.engineer_reasoning_output_tokens == 10
    assert sink.copilot_premium_requests == 7.5


def test_copilot_rate_is_configurable(monkeypatch) -> None:
    from argus.core.pricing import copilot_usd_per_premium_request

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.10")
    assert copilot_usd_per_premium_request() == 0.10
    # bad / negative input → fail-soft to the published default
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "nope")
    assert copilot_usd_per_premium_request() == 0.04
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "-1")
    assert copilot_usd_per_premium_request() == 0.04
