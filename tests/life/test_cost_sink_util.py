"""F3 PART B: the cost sink folds codex.util.completed events.

Several codex calls (manager stage/route/converse/domain-author, vertical
classify) were invisible to total_usd() and the daily cap. They now emit
``codex.util.completed``; this pins that the sink records + sums them.
"""
from __future__ import annotations

from argus_skill.core.pricing import usd_for_tokens
from argus_skill.life.supervisor._cost import _CostTrackingSink


class _Down:
    def handle_event(self, e: dict) -> None: ...
    def handle_stream_line(self, s: str, line: str) -> None: ...
    def close(self) -> None: ...


def _sink() -> _CostTrackingSink:
    return _CostTrackingSink(_Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5")


def _util_event(inp: int, out: int, *, model: str = "gpt-5.5") -> dict:
    return {
        "type": "codex.util.completed",
        "agent_layer": "manager",
        "model": model,
        "input_tokens": inp,
        "cached_input_tokens": 0,
        "output_tokens": out,
        "usage_scope": "delta",
    }


def _main_event(inp: int, out: int, *, reasoning_out: int = 0) -> dict:
    return {
        "type": "round.main.completed",
        "input_tokens": inp,
        "cached_input_tokens": 0,
        "output_tokens": out,
        "reasoning_output_tokens": reasoning_out,
        "usage_scope": "delta",
    }


def test_codex_util_event_records_tokens_and_folds_into_totals() -> None:
    sink = _sink()
    base = sink.total_usd()
    sink.handle_event(_util_event(1000, 100))
    assert sink.util_input_tokens == 1000
    assert sink.util_output_tokens == 100
    assert sink.total_input_tokens() == 1000        # util folded into the total
    assert sink.total_output_tokens() == 100
    assert sink.util_usd() >= 0.0
    assert sink.total_usd() == base + sink.util_usd()  # util folded into total_usd


def test_codex_util_events_accumulate_per_call() -> None:
    sink = _sink()
    sink.handle_event(_util_event(1000, 100))
    sink.handle_event(_util_event(500, 50))
    assert sink.util_input_tokens == 1500           # delta math sums per call
    assert sink.util_output_tokens == 150
    assert sink.util_usd() == sink.util_usd()       # stable; >= one-event value


def test_util_buckets_by_model() -> None:
    sink = _sink()
    sink.handle_event(_util_event(1000, 100, model="gpt-5.5"))
    sink.handle_event(_util_event(200, 20, model="haiku-4-5"))
    assert set(sink.util_usage_by_model) == {"gpt-5.5", "haiku-4-5"}
    assert sink.util_usage_by_model["gpt-5.5"][0] == 1000
    assert sink.util_usage_by_model["haiku-4-5"][0] == 200


def test_engineer_reasoning_tokens_increase_usd() -> None:
    without_reasoning = _sink()
    with_reasoning = _sink()
    without_reasoning.handle_event(_main_event(1000, 100, reasoning_out=0))
    with_reasoning.handle_event(_main_event(1000, 100, reasoning_out=25))
    assert without_reasoning.engineer_reasoning_output_tokens == 0
    assert with_reasoning.engineer_reasoning_output_tokens == 25
    assert with_reasoning.total_reasoning_output_tokens() == 25
    assert with_reasoning.engineer_usd() > without_reasoning.engineer_usd()
    assert with_reasoning.total_usd() > without_reasoning.total_usd()


def test_scientist_reasoning_tokens_increase_usd() -> None:
    sink = _sink()
    base = usd_for_tokens("gpt-5.5", 1000, 0, 100)
    sink.handle_event({
        "type": "skill.cost.completed",
        "matcher_model": "gpt-5.5",
        "distiller_model": "gpt-5.5-mini",
        "matcher": {
            "model": "gpt-5.5",
            "input_tokens": 1000,
            "cached_input_tokens": 0,
            "output_tokens": 100,
            "reasoning_output_tokens": 25,
        },
        "distiller": {
            "model": "gpt-5.5-mini",
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        "usage_scope": "delta",
    })
    assert sink.scientist_reasoning_output_tokens == 25
    assert sink.scientist_usd() > base
