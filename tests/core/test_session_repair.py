"""Synthetic original stores only; never opens a real provider or project."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from argus.core import session_repair
from argus.core.cost_control import _budget_reason, _default_state
from argus.core.provider_sessions import bind_before_dispatch, read_bindings
from argus.core.session_repair import repair_session
from argus.core.usage import UsageLedger, UsageRecord, build_usage_record


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    home = tmp_path / "copilot"
    monkeypatch.setenv("COPILOT_HOME", str(home))
    project = tmp_path / "projects" / "fixture-project"
    session = str(uuid4())
    call_id = "fixture-call"
    row = UsageRecord.from_jsonable({"call_id": call_id, "project_id": project.name,
        "provider": "copilot", "status": "error", "pricing_status": "partial",
        "cost_usd": None, "thread_id": None, "started_at": time.time() - 2,
        "completed_at": time.time(), "error": "synthetic watchdog interruption"})
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(row)
    start = {"type": "agent.io.start", "call_id": call_id, "backend": "copilot",
             "resume_thread_id": None}
    end = {"type": "agent.io.complete", "call_id": call_id, "thread_id": None, "exit_code": -9}
    write_rows(project / "events.jsonl", [start, end])
    events = [{"id": str(uuid4()), "type": "model.call_start", "data": {"turnId": "fixture-turn"}},
              {"id": str(uuid4()), "type": "model.call_finished", "data": {"turnId": "fixture-turn", "outcome": "success"}}]
    write_rows(project / "agent_io.jsonl", [start, *[
        {"type": "agent.io.stream", "call_id": call_id, "stream": "stdout", "line": json.dumps(e)} for e in events]])
    provider = home / "session-state" / session / "events.jsonl"
    write_rows(provider, [{"id": str(uuid4()), "type": "session.start", "data": {"sessionId": session}}, *events])
    return project, session, call_id, provider


def preview(evidence):
    project, session, call_id, _ = evidence
    return repair_session(project, call_id=call_id, session_id=session)


def apply(evidence, view=None):
    project, session, call_id, _ = evidence
    view = view or preview(evidence)
    return repair_session(project, call_id=call_id, session_id=session, dry_run=False,
        expected_row_hash=view["expected_row_hash"], expected_evidence_hash=view["evidence_hash"],
        reason="Synthetic operator decision after reviewing original evidence")


def test_preview_and_identity_only_apply_preserve_raw_row_and_gate(evidence):
    project, session, call_id, _ = evidence
    before = (project / "usage.jsonl").read_bytes()
    view = preview(evidence)
    assert not view["applied"] and view["matched_events"] == 2
    assert read_bindings(project)["decisions"] == []
    result = apply(evidence, view)
    assert result["applied"] and not result["billing_reconciled"]
    assert (project / "usage.jsonl").read_bytes() == before
    audit = read_bindings(project)["decisions"][0]
    assert audit["original_row"].encode() == before
    ledger = UsageLedger(project, migrate_legacy=False)
    assert ledger.ensure_copilot_usage_reconciled() == 0
    row = ledger.records()[0]
    assert row.thread_id == session and row.status == "error"
    assert row.accounting_pending == "cancelled_tail_unverified"
    assert row.cost_usd is None and row.pricing_status == "partial"
    assert _budget_reason([row], _default_state(time.time()), 100).startswith("unresolved provider cost")
    assert apply(evidence, view) == result
    assert len(read_bindings(project)["decisions"]) == 1


@pytest.mark.parametrize("filename", ["agent_io.jsonl", "events.jsonl"])
def test_missing_original_evidence_refused(evidence, filename):
    project, *_ = evidence
    (project / filename).unlink()
    with pytest.raises(FileNotFoundError):
        preview(evidence)
    assert not read_bindings(project)["decisions"]


def test_time_match_or_asserted_identity_alone_is_not_authority(evidence):
    project, *_ = evidence
    write_rows(project / "agent_io.jsonl", [{"type": "agent.io.start", "call_id": "fixture-call"}])
    with pytest.raises(ValueError, match="exact original"):
        preview(evidence)


@pytest.mark.parametrize("mutation", ["payload", "extra_request", "duplicate_id", "start_id"])
def test_provider_mismatch_ambiguity_or_tampering_refused(evidence, mutation):
    _, _, _, provider = evidence
    events = session_repair._rows(provider.read_bytes())
    if mutation == "payload":
        events[-1]["data"]["outcome"] = "forged"
    elif mutation == "extra_request":
        events.append({"id": str(uuid4()), "type": "model.call_start", "data": {}})
    elif mutation == "duplicate_id":
        events.append(events[-1])
    else:
        events[0]["data"]["sessionId"] = str(uuid4())
    write_rows(provider, events)
    with pytest.raises(ValueError):
        preview(evidence)


def test_duplicate_receipt_ids_in_another_session_refused(evidence):
    _, _, _, provider = evidence
    other = provider.parent.parent / str(uuid4()) / "events.jsonl"
    write_rows(other, session_repair._rows(provider.read_bytes()))
    with pytest.raises(ValueError, match="ambiguous"):
        preview(evidence)


@pytest.mark.parametrize("name", ["provider", "agent_io.jsonl", "events.jsonl", "usage.jsonl"])
def test_truncation_is_not_silently_ignored(evidence, name):
    project, _, _, provider = evidence
    path = provider if name == "provider" else project / name
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="truncated"):
        preview(evidence)


@pytest.mark.parametrize("source", ["ledger", "binding"])
def test_other_project_or_resumed_ownership_refused(evidence, source):
    project, session, _, _ = evidence
    other = project.parent / "other-project"
    if source == "ledger":
        row = UsageRecord.from_jsonable({"call_id": "other-call", "thread_id": session})
        UsageLedger(other, migrate_legacy=False).append(row)
    else:
        bind_before_dispatch(other, call_id="other-call", session_id=session, resumed=True)
    with pytest.raises(ValueError, match="another owner"):
        preview(evidence)


@pytest.mark.parametrize("target", ["row", "evidence"])
def test_preview_cas_rejects_concurrent_mutation(evidence, target):
    project, _, _, provider = evidence
    view = preview(evidence)
    if target == "row":
        rows = session_repair._rows((project / "usage.jsonl").read_bytes())
        rows[0]["error"] = "changed concurrently"
        write_rows(project / "usage.jsonl", rows)
    else:
        provider.write_bytes(provider.read_bytes() + b'{}\n')
    with pytest.raises(ValueError, match="stale"):
        apply(evidence, view)
    assert not read_bindings(project)["decisions"]


def test_audit_failure_exposes_no_partial_binding(evidence, monkeypatch):
    project, *_ = evidence
    before = (project / "usage.jsonl").read_bytes()
    view = preview(evidence)
    def fail(*args):
        raise OSError("synthetic audit failure")
    monkeypatch.setattr(session_repair, "write_bindings", fail)
    with pytest.raises(OSError):
        apply(evidence, view)
    assert (project / "usage.jsonl").read_bytes() == before
    assert not read_bindings(project)["decisions"]


def test_concurrent_apply_is_idempotent(evidence):
    view = preview(evidence)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: apply(evidence, view), range(4)))
    assert all(result == results[0] for result in results)
    assert len(read_bindings(evidence[0])["decisions"]) == 1


def test_repaired_session_cannot_be_resumed_into_new_call(evidence):
    apply(evidence)
    project, session, *_ = evidence
    with pytest.raises(ValueError, match="quarantined"):
        bind_before_dispatch(project, call_id="new-call", session_id=session, resumed=True)


def test_public_path_traversal_is_not_a_session(evidence):
    project, _, call_id, _ = evidence
    with pytest.raises(ValueError):
        repair_session(project, call_id=call_id, session_id="../../private-file")


def test_symlink_evidence_refused(evidence):
    project, _, _, provider = evidence
    copy = project / "copy.jsonl"
    copy.write_bytes(provider.read_bytes())
    provider.unlink()
    provider.symlink_to(copy)
    with pytest.raises(ValueError, match="symlink"):
        preview(evidence)


def test_missing_aiu_and_priced_cancelled_tail_both_remain_pending(tmp_path):
    for nano in (None, 100):
        row = build_usage_record(call_id="cancelled", project_root=tmp_path,
            mission_id=None, provider="copilot", model="fixture", run_label="engineer-r1",
            started_at=1, completed_at=2, status="error", thread_id="fixture-session",
            total_nano_aiu=nano, copilot_token_billing_expected=True)
        assert row.accounting_pending == "cancelled_tail_unverified" and row.pricing_status == "partial"
        assert _budget_reason([row], _default_state(time.time()), 100).startswith("unresolved provider cost")
        assert row.cost_usd == (None if nano is None else nano / 100_000_000_000)


def test_real_monetary_budget_denial_remains(tmp_path):
    row = UsageRecord.from_jsonable({"call_id": "priced", "provider": "codex", "status": "completed",
                                   "pricing_status": "priced", "cost_usd": 10})
    reason = _budget_reason([row], _default_state(time.time()), 1)
    assert "budget exhausted" in reason
    assert "unresolved" not in reason
    assert replace(row, cost_usd=None, pricing_status="unpriced").cost_usd is None


def test_late_billing_does_not_claim_cancelled_tail_settled(evidence, monkeypatch):
    import sqlite3
    project, session, _, provider = evidence
    view = preview(evidence)
    apply(evidence, view)
    db = provider.parents[2] / "session-store.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY, session_id TEXT, turn_index INTEGER, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, total_nano_aiu INTEGER, request_multiplier REAL, created_at TEXT)")
        connection.execute("INSERT INTO assistant_usage_events VALUES (1, ?, 0, 'fixture', 1, 1, 0, 0, 0, NULL, 1, '2026-09-17T00:00:00Z')", (session,))
    ledger = UsageLedger(project, migrate_legacy=False)
    for nano in (None, 20, 30):
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE assistant_usage_events SET total_nano_aiu=?", (nano,))
        assert ledger.ensure_copilot_usage_reconciled() == 0
        row = ledger.records()[0]
        assert row.cost_usd is None and not row.model_usage
        assert row.accounting_pending == "cancelled_tail_unverified"
        assert _budget_reason([row], _default_state(time.time()), 100).startswith("unresolved provider cost")
    assert apply(evidence, view)["applied"]
    assert len(read_bindings(project)["decisions"]) == 1


def test_post_rename_failure_recovers_one_complete_decision(evidence, monkeypatch):
    project, *_ = evidence
    view = preview(evidence)
    writer = session_repair.write_bindings
    def committed_then_error(*args):
        writer(*args)
        raise OSError("synthetic directory fsync failure after atomic replace")
    monkeypatch.setattr(session_repair, "write_bindings", committed_then_error)
    with pytest.raises(OSError):
        apply(evidence, view)
    assert len(read_bindings(project)["decisions"]) == 1
    monkeypatch.setattr(session_repair, "write_bindings", writer)
    assert apply(evidence, view)["applied"]
    assert len(read_bindings(project)["decisions"]) == 1


def test_replace_failure_leaves_old_snapshot(evidence, monkeypatch):
    from argus.core import provider_sessions
    project, *_ = evidence
    view = preview(evidence)
    def fail(*args):
        raise OSError("synthetic atomic rename failure")
    monkeypatch.setattr(provider_sessions.os, "replace", fail)
    with pytest.raises(OSError):
        apply(evidence, view)
    assert read_bindings(project)["decisions"] == []


def test_changed_original_after_apply_is_not_silently_reaccepted(evidence):
    project, *_ = evidence
    view = preview(evidence)
    apply(evidence, view)
    rows = session_repair._rows((project / "usage.jsonl").read_bytes())
    rows[0]["cost_usd"] = 123
    write_rows(project / "usage.jsonl", rows)
    with pytest.raises(ValueError, match="changed"):
        apply(evidence, view)
    with pytest.raises(ValueError, match="changed"):
        UsageLedger(project, migrate_legacy=False).records()


def test_resume_start_and_existing_receipt_ownership_rejected(evidence):
    project, *_ = evidence
    rows = session_repair._rows((project / "events.jsonl").read_bytes())
    rows[0]["resume_thread_id"] = "existing-session"
    write_rows(project / "events.jsonl", rows)
    with pytest.raises(ValueError, match="resumed"):
        preview(evidence)


def test_duplicate_model_usage_is_not_reassigned(evidence):
    project, session, *_ = evidence
    rows = session_repair._rows((project / "usage.jsonl").read_bytes())
    rows[0]["model_usage"] = [{"usage_event_id": 1, "session_id": session}]
    write_rows(project / "usage.jsonl", rows)
    with pytest.raises(ValueError, match="receipt ownership"):
        preview(evidence)


def test_receipt_owner_with_missing_thread_in_another_project_refused(evidence):
    project, session, *_ = evidence
    other = project.parent / "other-project"
    row = UsageRecord.from_jsonable({"call_id": "other-call", "thread_id": None,
        "model_usage": [{"session_id": session, "usage_event_id": 7}]})
    UsageLedger(other, migrate_legacy=False).append(row)
    with pytest.raises(ValueError, match="another owner"):
        preview(evidence)


def test_unidentified_terminal_identity_mismatch_is_not_ignored(evidence):
    project, _, call_id, _ = evidence
    rows = session_repair._rows((project / "agent_io.jsonl").read_bytes())
    rows.append({"type": "agent.io.stream", "call_id": call_id, "stream": "stdout",
                 "line": json.dumps({"type": "result", "sessionId": str(uuid4()), "exitCode": 0})})
    write_rows(project / "agent_io.jsonl", rows)
    with pytest.raises(ValueError, match="conflicting session"):
        preview(evidence)


def test_ordinary_failed_call_collects_late_receipts_but_not_full_settlement(tmp_path, monkeypatch):
    import sqlite3
    from datetime import UTC, datetime

    from tests.provider_integrations.test_copilot_usage import _db, _insert
    home = tmp_path / "fixture-home"
    db = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    project = tmp_path / "projects" / "ordinary"
    now = time.time()
    row = build_usage_record(call_id="ordinary-call", project_root=project,
        mission_id=None, provider="copilot", model="fixture", run_label="engineer-r1",
        started_at=now - 2, completed_at=now, status="error", thread_id="ordinary-session",
        copilot_token_billing_expected=True)
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(row)
    created = datetime.fromtimestamp(now - 1, UTC).isoformat()
    _insert(db, session="ordinary-session", model="fixture", created_at=created,
            input_tokens=2, output_tokens=1, total_nano_aiu=10)
    assert ledger.ensure_copilot_usage_reconciled() == 1
    first = ledger.records()[0]
    assert first.cost_usd == 10 / 100_000_000_000 and first.pricing_status == "partial"
    assert ledger.ensure_copilot_usage_reconciled() == 0
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE assistant_usage_events SET total_nano_aiu=20")
    assert ledger.ensure_copilot_usage_reconciled() == 1
    assert ledger.records()[0].cost_usd == 20 / 100_000_000_000
    _insert(db, session="ordinary-session", model="fixture", created_at=created,
            input_tokens=3, output_tokens=1, total_nano_aiu=30)
    assert ledger.ensure_copilot_usage_reconciled() == 1
    observed = ledger.records()[0]
    assert observed.cost_usd == 50 / 100_000_000_000 and len(observed.model_usage) == 2
    assert observed.pricing_status == "partial" and observed.accounting_pending
    assert _budget_reason([observed], _default_state(time.time()), 100).startswith("unresolved provider cost")
    assert ledger.ensure_copilot_usage_reconciled() == 0


def test_duplicate_json_keys_cannot_hide_tampering(evidence):
    _, _, _, provider = evidence
    raw = provider.read_text()
    provider.write_text(raw.replace('"outcome": "success"', '"outcome": "forged", "outcome": "success"'))
    with pytest.raises(ValueError, match="duplicate keys"):
        preview(evidence)
