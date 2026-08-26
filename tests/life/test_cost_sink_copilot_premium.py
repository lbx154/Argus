"""Copilot premium-request metering (接入 copilot 成本可控).

Copilot bills flat PREMIUM REQUESTS, not tokens, and reports NO input tokens —
so the token-based USD meter reads $0 for a copilot run, leaving the F3 breaker /
daily cap blind. These tests pin the fix end to end:

  * the backend extracts copilot's session-cumulative ``premiumRequests`` and
    de-cumulates it into a per-call delta (like it already does for codex tokens);
  * the cost sink folds those deltas into ``copilot_usd()`` (priced per the
    configurable overage rate) and therefore into ``total_usd()`` — so copilot
    spend flows through the SAME breaker with no new control surface.
"""
from __future__ import annotations

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.adapters.agent_cli_backend._result import (
    _extract_copilot_premium_requests,
)
from argus_skill.life.supervisor._cost import _CostTrackingSink


class _Down:
    def handle_event(self, e: dict) -> None: ...
    def handle_stream_line(self, s: str, line: str) -> None: ...
    def close(self) -> None: ...


def _sink() -> _CostTrackingSink:
    return _CostTrackingSink(_Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5")


# --- extractor: picks the LAST (cumulative) premiumRequests -------------------

def test_extract_copilot_premium_requests_takes_last_cumulative() -> None:
    events = [
        {"type": "assistant.message", "data": {"content": "hi", "outputTokens": 5}},
        {"type": "result", "usage": {"premiumRequests": 7.5}},
    ]
    assert _extract_copilot_premium_requests(events) == (7.5, True)
    # a resumed turn reports the running total; we take the latest
    events.append({"type": "result", "usage": {"premiumRequests": 15}})
    assert _extract_copilot_premium_requests(events) == (15.0, True)
    # no result / no usage → 0.0 with present=False, fail-soft (codex/claude streams)
    assert _extract_copilot_premium_requests([{"type": "turn.completed"}]) == (0.0, False)
    assert _extract_copilot_premium_requests(None) == (0.0, False)
    # bool must not be mistaken for a number
    assert _extract_copilot_premium_requests(
        [{"type": "result", "usage": {"premiumRequests": True}}]
    ) == (0.0, False)


# --- adapter: de-cumulates the running total into per-call deltas -------------

def test_premium_delta_per_thread_decumulates() -> None:
    be = AgentCliBackend(backend="copilot")
    assert be._premium_delta_for_thread(thread_id="t1", raw_total=7.5) == 7.5
    assert be._premium_delta_for_thread(thread_id="t1", raw_total=15.0) == 7.5
    assert be._premium_delta_for_thread(thread_id="t1", raw_total=15.0) == 0.0  # no new spend
    # no thread id → cannot de-cumulate; charge the raw total once
    assert be._premium_delta_for_thread(thread_id=None, raw_total=3.0) == 3.0
    # counter reset (new session reuses id) → charge current total, never negative
    assert be._premium_delta_for_thread(thread_id="t2", raw_total=5.0) == 5.0
    assert be._premium_delta_for_thread(thread_id="t2", raw_total=2.0) == 2.0
    # zero/absent premium → 0.0 (codex/claude)
    assert be._premium_delta_for_thread(thread_id="t3", raw_total=0.0) == 0.0


def test_premium_delta_after_restart_fails_closed_then_recovers() -> None:
    be = AgentCliBackend(backend="copilot")

    assert be._premium_delta_for_thread(
        thread_id="resumed",
        raw_total=15.0,
        resume_baseline_unknown=True,
    ) is None
    assert be._premium_delta_for_thread(
        thread_id="resumed",
        raw_total=22.5,
        resume_baseline_unknown=True,
    ) == 7.5


# --- sink: folds premium deltas into copilot_usd() and total_usd() -----------

def test_sink_folds_copilot_premium_into_total_usd() -> None:
    sink = _sink()
    base = sink.total_usd()
    sink.handle_event({
        "type": "round.main.completed", "premium_requests": 7.5, "usage_scope": "delta",
    })
    sink.handle_event({
        "type": "round.review.completed", "premium_requests": 7.5,
        "usage_scope": "delta", "status": "continue",
    })
    assert sink.copilot_premium_requests == 15.0     # engineer + reviewer summed
    assert sink.copilot_usd() == 15.0 * 0.04         # default overage rate
    assert sink.total_usd() == base + sink.copilot_usd()


def test_sink_zero_premium_is_free_for_codex() -> None:
    # codex/claude round events carry no premium_requests → copilot cost stays 0.
    sink = _sink()
    sink.handle_event({
        "type": "round.main.completed", "input_tokens": 100, "output_tokens": 10,
        "usage_scope": "delta",
    })
    assert sink.copilot_premium_requests == 0.0
    assert sink.copilot_usd() == 0.0


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
    assert sink.copilot_usd() == 15.0 * 0.04
    assert sink.total_usd() >= sink.copilot_usd()


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
    from argus_skill.life.supervisor import _cost

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.10")
    assert _cost._copilot_usd_per_premium_request() == 0.10
    # bad / negative input → fail-soft to the published default
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "nope")
    assert _cost._copilot_usd_per_premium_request() == 0.04
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "-1")
    assert _cost._copilot_usd_per_premium_request() == 0.04
