"""Mission usage is read once from the call ledger."""
from __future__ import annotations

from dataclasses import replace

from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, build_usage_record
from argus.life.supervisor._cost import _CostTrackingSink


class _Down:
    def handle_event(self, e: dict) -> None: ...
    def handle_stream_line(self, s: str, line: str) -> None: ...
    def close(self) -> None: ...


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
