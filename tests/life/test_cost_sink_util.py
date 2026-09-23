"""Mission usage includes utility and scientist calls."""
from __future__ import annotations

from dataclasses import replace

from argus.core.pricing import usd_for_tokens
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, build_usage_record
from argus.life.supervisor._cost import _CostTrackingSink


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
    summary, costs = sink.completion_usage()
    assert summary.input_tokens == 1000
    assert summary.output_tokens == 100
    assert costs["util_cost_usd"] >= 0.0
    assert summary.known_cost_usd == base + costs["util_cost_usd"]


def test_codex_util_events_accumulate_per_call() -> None:
    sink = _sink()
    sink.handle_event(_util_event(1000, 100))
    sink.handle_event(_util_event(500, 50))
    assert sink.util_input_tokens == 1500           # delta math sums per call
    assert sink.util_output_tokens == 150
    assert sink.completion_usage()[1]["util_cost_usd"] == usd_for_tokens("gpt-5.5", 1500, 0, 150)


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
    assert with_reasoning.usage_summary().reasoning_output_tokens == 25
    _, base_costs = without_reasoning.completion_usage()
    _, reasoning_costs = with_reasoning.completion_usage()
    assert reasoning_costs["engineer_cost_usd"] > base_costs["engineer_cost_usd"]
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
    assert sink.completion_usage()[1]["scientist_cost_usd"] > base


def test_completion_totals_and_details_share_one_ledger_read(tmp_path, monkeypatch):
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    record = build_usage_record(
        call_id="first", project_root=tmp_path, mission_id="mission",
        provider="codex", model="gpt-5.5", run_label="matcher",
        started_at=1.0, completed_at=2.0, status="completed",
        token_usage=TokenUsage(
            input_tokens=1000, cached_input_tokens=100, cache_write_tokens=20,
            output_tokens=50, reasoning_output_tokens=10,
            input_tokens_present=True, output_tokens_present=True,
            cached_input_tokens_present=True, cache_write_tokens_present=True,
            reasoning_output_tokens_present=True,
        ),
    )
    ledger.append(record)
    read = ledger.records
    reads = []

    def read_then_append(**filters):
        rows = read(**filters)
        reads.append(filters)
        ledger.append(replace(record, call_id="later"))
        return rows

    monkeypatch.setattr(ledger, "records", read_then_append)
    sink = _CostTrackingSink(
        _Down(), engineer_model="gpt-5.5", reviewer_model="gpt-5.5",
        usage_ledger=ledger, mission_id="mission",
    )
    summary, details = sink.completion_usage()

    assert reads == [{"mission_id": "mission"}]
    assert summary.call_count == 1 and len(read(mission_id="mission")) == 2
    assert summary.known_cost_usd == details["scientist_cost_usd"] == record.cost_usd
    assert summary.input_tokens == details["scientist_input_tokens"] == 1000
    assert summary.cache_write_tokens == 20
    assert details["scientist_usage_by_model"]["gpt-5.5"] == {
        "input_tokens": 1000, "cached_input_tokens": 100,
        "output_tokens": 50, "reasoning_output_tokens": 10,
    }
