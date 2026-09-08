"""Issue #113: only trusted, unmetered top-level parser refusals are free."""
import json
import time

import pytest

from argus_skill.core.cost_control import cost_control_snapshot, reserve_call_budget
from argus_skill.core.token_usage import TokenUsage
from argus_skill.core.usage import UsageLedger, UsageRecord, build_usage_record

ERROR = ("Process exited with code 1 before turn completion.\n"
         "error: unknown option '--context'\n(Did you mean --connect?)\n\n"
         "Try 'copilot --help' for more information.")


def row():
    return dict(call_id="parser-call", project_id="p1", provider="copilot",
                model="gpt-6-astra", run_label="manager-classify-grounded-retry",
                started_at=time.time()-2, completed_at=time.time()-1,
                status="error", source="run_exec", error=ERROR,
                cost_usd=None, pricing_status="partial", thread_id=None)


def receipt():
    return dict(type="agent.io.complete", call_id="parser-call", backend="copilot",
                run_label="manager-classify-grounded-retry", exit_code=1,
                thread_id=None, turn_completed=False, turn_failed=True,
                fatal_error="Process exited with code 1 before turn completion.",
                tool_activity_observed=False, agent_message_count=0,
                stdout_line_count=0, json_event_count=0,
                command=["/usr/bin/copilot", "--context", "default"])


def project(tmp_path, r=None, event=None):
    p = tmp_path / "projects" / "p1"
    p.mkdir(parents=True)
    (p / "usage.jsonl").write_text(json.dumps(r or row()) + "\n")
    (p / "events.jsonl").write_text(json.dumps(event or receipt()) + "\n")
    return p


def test_historical_projection_is_idempotent_and_preserves_ledger(tmp_path):
    p = project(tmp_path)
    before = (p / "usage.jsonl").read_bytes()
    events_before = (p / "events.jsonl").read_bytes()
    for migrate_legacy in (False, True, True):
        record = UsageLedger(p, migrate_legacy=migrate_legacy).records()[0]
        assert (record.pricing_status, record.cost_usd) == ("not_billed", 0)
        assert (record.status, record.pricing_tier) == ("error", "not_started")
    assert (p / "usage.jsonl").read_bytes() == before
    assert (p / "events.jsonl").read_bytes() == events_before


def test_identical_text_without_receipt_is_not_evidence():
    assert UsageRecord.from_jsonable(row()).pricing_status == "partial"


@pytest.mark.parametrize("change", [
    {"agent_message_count": 1}, {"stdout_line_count": 1}, {"json_event_count": 1},
    {"tool_activity_observed": True}, {"turn_completed": True},
    {"thread_id": "session"}, {"exit_code": 0}, {"backend": "codex"},
    {"call_id": "other"}, {"command": ["copilot", "--prompt", "--context"]},
    {"run_label": "foreign-label"}, {"turn_failed": False},
    {"fatal_error": "tool failed"}, {"type": "tool.result"},
])
def test_untrusted_or_post_start_receipt_does_not_convert(tmp_path, change):
    p = project(tmp_path, event={**receipt(), **change})
    assert UsageLedger(p, migrate_legacy=False).records()[0].pricing_status == "partial"


@pytest.mark.parametrize("change", [
    {"input_tokens": 1}, {"cached_input_tokens": 1}, {"output_tokens": 1},
    {"cache_write_tokens": 1}, {"reasoning_output_tokens": 1},
    {"premium_requests": 0}, {"premium_requests_present": True},
    {"total_nano_aiu": 0}, {"cost_usd": 0}, {"model_usage": [{"input_tokens": 1}]},
])
def test_observed_receipt_usage_cannot_be_waived_by_an_incomplete_ledger_row(tmp_path, change):
    p = project(tmp_path, event={**receipt(), **change})
    assert UsageLedger(p, migrate_legacy=False).records()[0].pricing_status == "partial"


@pytest.mark.parametrize("change", [
    {"input_tokens": 0}, {"input_tokens": 9}, {"cached_input_tokens": 1},
    {"cache_write_tokens": 1}, {"output_tokens": 1}, {"reasoning_output_tokens": 1},
    {"premium_requests": 1}, {"premium_requests": 0}, {"total_nano_aiu": 0},
    {"model_usage": [{"input_tokens": 1}]}, {"cost_usd": .1},
    {"premium_request_cost_usd": .04}, {"thread_id": "session"},
    {"source": "legacy.events"}, {"status": "completed"},
])
def test_any_observed_usage_or_inconsistent_row_is_preserved(tmp_path, change):
    p = project(tmp_path, r={**row(), **change})
    record = UsageLedger(p, migrate_legacy=False).records()[0]
    assert record.pricing_status == "partial"
    assert record.cost_usd == change.get("cost_usd")


@pytest.mark.parametrize("error", ["network timeout", "unknown failure", "",
                                      "Tool said: " + ERROR, ERROR + "\nmodel output"])
def test_unknown_network_and_quoted_text_still_block(tmp_path, error):
    project(tmp_path, r={**row(), "error": error})
    snap = cost_control_snapshot(global_root=tmp_path)
    assert snap["unresolved_calls"] == 1


def test_cross_project_admission_releases_only_parser_item(tmp_path):
    p = project(tmp_path)
    p2 = tmp_path / "projects" / "p2"
    p2.mkdir()
    for target in (p, p2):
        reservation, reason = reserve_call_budget(
            call_id="probe-" + target.name, project_root=target, mission_id=None,
            provider="copilot", model="gpt-6-astra", run_label="test",
            global_root=tmp_path)
        assert reservation is not None, reason
        reservation.release(reason="test complete")
    unknown = {**row(), "call_id": "unknown", "error": "network timeout"}
    (p2 / "usage.jsonl").write_text(json.dumps(unknown) + "\n")
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 1


def test_new_failure_uses_the_same_trusted_receipt(tmp_path):
    r = row()
    record = build_usage_record(
        call_id=r["call_id"], project_root=tmp_path, mission_id=None,
        provider="copilot", model=r["model"], run_label=r["run_label"],
        started_at=r["started_at"], completed_at=r["completed_at"], status="error",
        error=ERROR, startup_receipt=receipt())
    assert (record.pricing_status, record.cost_usd) == ("not_billed", 0)


def test_duplicate_or_missing_completion_receipt_fails_closed(tmp_path):
    p = project(tmp_path)
    path = p / "events.jsonl"
    original = path.read_text()
    for text in ("", original + original):
        path.write_text(text)
        assert UsageLedger(p, migrate_legacy=False).records()[0].pricing_status == "partial"


@pytest.mark.parametrize("field", ["premium_requests", "total_nano_aiu", "provider_cost_usd"])
@pytest.mark.parametrize("value", [0, 1])
def test_new_failure_retains_observed_metering(tmp_path, field, value):
    r = row()
    record = build_usage_record(
        call_id=r["call_id"], project_root=tmp_path, mission_id=None,
        provider="copilot", model=r["model"], run_label=r["run_label"],
        started_at=r["started_at"], completed_at=r["completed_at"], status="error",
        error=ERROR, startup_receipt=receipt(), **{field: value})
    assert record.pricing_status != "not_billed"


@pytest.mark.parametrize("field", [
    "input_tokens", "cached_input_tokens", "cache_write_tokens",
    "output_tokens", "reasoning_output_tokens",
])
@pytest.mark.parametrize("value", [0, 1])
def test_new_failure_preserves_all_observed_token_fields(tmp_path, field, value):
    r = row()
    usage = TokenUsage(**{field: value, field + "_present": True})
    record = build_usage_record(
        call_id=r["call_id"], project_root=tmp_path, mission_id=None,
        provider="copilot", model=r["model"], run_label=r["run_label"],
        started_at=r["started_at"], completed_at=r["completed_at"], status="error",
        error=ERROR, startup_receipt=receipt(), token_usage=usage)
    assert record.pricing_status != "not_billed"
    assert getattr(record, field) == value
