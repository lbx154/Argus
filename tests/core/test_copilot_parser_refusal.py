"""Issue #113: only trusted, unmetered top-level parser refusals are free."""
import json
import time

import pytest

from argus.core.usage import UsageLedger

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


def test_duplicate_or_missing_completion_receipt_fails_closed(tmp_path):
    p = project(tmp_path)
    path = p / "events.jsonl"
    original = path.read_text()
    for text in ("", original + original):
        path.write_text(text)
        assert UsageLedger(p, migrate_legacy=False).records()[0].pricing_status == "partial"
