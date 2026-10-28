from __future__ import annotations

import gc
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from argus.core.token_usage import TokenUsage, extract_token_usage
from argus.core.usage import (
    UsageLedger,
    UsageRecord,
    build_usage_record,
    format_usage_cost,
)


def _fixture() -> dict:
    path = Path(__file__).parents[1] / "fixtures" / "copilot_usage_real.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _known_usage(*, input_tokens: int, output_tokens: int) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_tokens_present=True,
        output_tokens_present=True,
        source="test",
    )


def _record(**overrides) -> UsageRecord:
    defaults = dict(
        mission_id="mission-1",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
    )
    return build_usage_record(**(defaults | overrides))


def test_usage_process_caches_are_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.core import usage

    monkeypatch.setattr(usage, "_CALL_ID_CACHE_MAX_PROJECTS", 2)
    monkeypatch.setattr(usage, "_CALL_ID_CACHE_MAX_IDS", 2)
    with usage._CALL_ID_CACHE_LOCK:
        usage._CALL_ID_CACHE.clear()

    ledgers = [UsageLedger(tmp_path / f"project-{index}") for index in range(3)]
    for index, ledger in enumerate(ledgers):
        ledger._cache_call_ids({f"call-{index}"})

    assert len(usage._CALL_ID_CACHE) == 2
    assert str(ledgers[0].path.resolve()) not in usage._CALL_ID_CACHE

    oversized = UsageLedger(tmp_path / "oversized")
    oversized._cache_call_ids({"call-a", "call-b", "call-c"})
    assert str(oversized.path.resolve()) not in usage._CALL_ID_CACHE

    lock_key = str(ledgers[0].lock_path.resolve())
    with ledgers[0]._locked():
        assert lock_key in usage._THREAD_LOCKS
    gc.collect()
    assert lock_key not in usage._THREAD_LOCKS


def test_real_copilot_fixture_preserves_matcher_and_scientist_output_tokens() -> None:
    fixture = _fixture()
    matcher = extract_token_usage(fixture["matcher"]["json_events"])
    scientist = extract_token_usage(fixture["scientist"]["json_events"])
    assert matcher.output_tokens == 118
    assert scientist.output_tokens == 13_175


def test_usage_reconciliation_uses_a_process_lock_without_fcntl(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core import usage

    calls = []
    monkeypatch.setattr(usage, "fcntl", None)
    monkeypatch.setattr(usage.portalocker, "lock", lambda fd, flags: calls.append(("lock", fd)))
    monkeypatch.setattr(usage.portalocker, "unlock", lambda fd: calls.append(("unlock", fd)))
    with UsageLedger(tmp_path, migrate_legacy=False)._locked():
        assert [name for name, _fd in calls] == ["lock"]
    assert [name for name, _fd in calls] == ["lock", "unlock"]
    assert calls[0][1] == calls[1][1]


def test_token_reconciliation_keeps_a_provider_settlement_that_arrives_after_the_read(
    tmp_path: Path,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    completed = _record(
        call_id="late-provider",
        project_root=tmp_path,
        mission_id=None,
        provider="opencode",
        model="gpt-5.5",
        run_label="manager",
        token_usage=_known_usage(input_tokens=100, output_tokens=20),
        provider_cost_usd=0.0123,
    )
    stale = replace(
        completed, cost_usd=None, pricing_status="unpriced", cost_basis="token",
    )
    ledger.append(completed)
    original = ledger.path.read_bytes()

    assert ledger._reconcile_token_pricing([stale]) == 0
    assert ledger.path.read_bytes() == original
    assert ledger.records() == [completed]


def test_the_usage_ledger_keeps_the_whole_call_record(tmp_path: Path) -> None:
    """usage.jsonl is the only copy of a call's usage; the event log no longer repeats it."""
    project = tmp_path / "projects" / "p1"
    record = _record(
        call_id="call-1",
        project_root=project,
        provider="copilot",
        run_label="simple-1",
        started_at=10.0,
        completed_at=11.25,
        token_usage=_known_usage(input_tokens=100, output_tokens=20),
        premium_requests=1.0,
        total_nano_aiu=2_000_000_000,
        thread_id="thread-1",
        model_usage=[{
            "usage_event_id": 7,
            "session_id": "thread-1",
            "model": "gpt-5.6-sol",
            "turn_index": 0,
            "input_tokens": 100,
            "output_tokens": 20,
            "total_nano_aiu": 2_000_000_000,
        }],
    )

    ledger = UsageLedger(project, migrate_legacy=False)
    assert ledger.append(record) is True
    stored = ledger.records()[0]
    assert (stored.project_id, stored.thread_id) == ("p1", "thread-1")
    assert stored.cost_basis == "token" and stored.cost_usd == pytest.approx(0.02)
    assert stored.duration_ms == 1250
    assert stored.model_usage[0]["total_nano_aiu"] == 2_000_000_000
    assert stored.model_usage[0]["usage_event_id"] == 7
    assert stored.model_usage[0]["session_id"] == "thread-1"


def test_summary_deduplicates_copilot_usage_event_across_calls(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    usage = {
        "usage_event_id": 42,
        "session_id": "session-1",
        "model": "gpt-5.6-sol",
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 10,
        "reasoning_output_tokens": 2,
        "total_nano_aiu": 1_000_000_000,
        "cost_usd": 0.01,
    }
    for call_id in ("call-1", "call-2"):
        ledger.append(_record(
            call_id=call_id,
            project_root=project,
            provider="copilot",
            run_label=call_id,
            token_usage=_known_usage(input_tokens=100, output_tokens=10),
            total_nano_aiu=1_000_000_000,
            model_usage=[usage],
        ))

    summary = ledger.summary(mission_id="mission-1")

    assert summary.call_count == 2
    assert summary.input_tokens == 100
    assert summary.cached_input_tokens == 20
    assert summary.output_tokens == 10
    assert summary.reasoning_output_tokens == 2
    assert summary.total_nano_aiu == 1_000_000_000
    assert summary.cost_usd == pytest.approx(0.01)


def test_reconciles_legacy_copilot_request_cost_with_exact_token_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copilot_home = tmp_path / "copilot"
    copilot_home.mkdir()
    db = copilot_home / "session-store.db"
    monkeypatch.setenv("COPILOT_HOME", str(copilot_home))
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assistant_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                total_nano_aiu INTEGER,
                request_multiplier REAL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO assistant_usage_events (
                session_id, turn_index, model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, reasoning_tokens,
                total_nano_aiu, request_multiplier, created_at
            ) VALUES ('session-1', 0, 'gpt-5.6-sol', 25819, 8, 0, 0, 0,
                      16160500000, 1.0, '2026-07-11T09:59:25.919Z')
            """
        )
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    old = UsageRecord(
        call_id="call-1",
        project_id="p1",
        mission_id=None,
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="simple-1",
        thread_id="session-1",
        started_at=1_783_763_961.9,
        completed_at=1_783_763_965.95,
        status="completed",
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_output_tokens=None,
        premium_requests=1.0,
        pricing_status="priced",
        pricing_tier="premium_request",
        cost_usd=0.04,
        cost_basis="premium_request",
    )
    UsageLedger(project, migrate_legacy=False).append(old)

    summary = UsageLedger(project).summary()
    assert summary.input_tokens == 25_819
    assert summary.output_tokens == 8
    assert summary.cost_usd == pytest.approx(0.161605)
    record = UsageLedger(project).records()[0]
    assert record.pricing_tier == "copilot_token"
    assert record.premium_request_cost_usd == pytest.approx(0.04)


def test_copilot_reconcile_does_not_reuse_usage_or_price_denials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copilot_home = tmp_path / "copilot"
    copilot_home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(copilot_home))
    with sqlite3.connect(copilot_home / "session-store.db") as conn:
        conn.execute(
            """
            CREATE TABLE assistant_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                total_nano_aiu INTEGER,
                request_multiplier REAL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO assistant_usage_events (
                session_id, turn_index, model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, reasoning_tokens,
                total_nano_aiu, request_multiplier, created_at
            ) VALUES ('session-1', 0, 'gpt-5.6-sol', 1000, 100, 0, 0, 0,
                      8000000000, 1.0, '2026-07-11T09:59:25.919Z')
            """
        )
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    ledger = UsageLedger(project, migrate_legacy=False)
    first = UsageRecord(
        call_id="completed",
        project_id="p1",
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=1_783_763_961.9,
        completed_at=1_783_763_965.95,
        status="completed",
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_output_tokens=None,
        premium_requests=1.0,
        pricing_status="partial",
        pricing_tier="premium_request_only",
        cost_usd=None,
        cost_basis="none",
        thread_id="session-1",
    )
    ledger.append(first)
    assert UsageLedger(project).records()[0].cost_usd == pytest.approx(0.08)

    second = _record(
        call_id="denied",
        project_root=project,
        provider="copilot",
        run_label="reviewer",
        started_at=1_783_763_965.96,
        completed_at=1_783_763_965.97,
        status="denied",
        error="provider copilot is cooling down after budget fence breach",
    )
    ledger.append(second)
    rows = [
        json.loads(line)
        for line in ledger.path.read_text(encoding="utf-8").splitlines()
    ]
    rows[-1].update(
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "total_nano_aiu": 8_000_000_000,
            "model_usage": rows[0]["model_usage"],
            "cost_usd": 0.08,
            "cost_basis": "token",
            "pricing_status": "priced",
            "pricing_tier": "copilot_token",
        }
    )
    ledger.path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    stat = ledger.path.stat()
    ledger.copilot_reconcile_path.write_text(
        json.dumps(
            {
                "version": 1,
                "usage_signature": [stat.st_ino, stat.st_size, stat.st_mtime_ns],
                "updated": 0,
            }
        ),
        encoding="utf-8",
    )

    records = UsageLedger(project).records()
    completed, denied = records
    assert completed.cost_usd == pytest.approx(0.08)
    assert denied.pricing_status == "not_billed"
    assert denied.cost_usd == 0.0
    assert denied.input_tokens is None
    assert denied.model_usage == ()
    marker = json.loads(ledger.copilot_reconcile_path.read_text(encoding="utf-8"))
    assert marker["version"] == 5

    third = replace(first, call_id="second-completed")
    ledger.append(third)
    records = UsageLedger(project).records()
    assert records[-1].total_nano_aiu is None
    assert records[-1].pricing_status == "priced"
    assert records[-1].cost_basis == "premium_request"
    assert records[-1].cost_usd == pytest.approx(0.04)


def test_missing_usage_and_unknown_model_are_never_rendered_as_zero(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p"
    missing = _record(
        call_id="missing",
        project_root=project,
        mission_id="m",
        provider="codex",
        status="error",
        token_usage=TokenUsage(),
    )
    unknown = _record(
        call_id="unknown",
        project_root=project,
        mission_id="m",
        provider="codex",
        model="future-model",
        run_label="reviewer",
        token_usage=_known_usage(input_tokens=100, output_tokens=20),
    )
    denied = _record(
        call_id="denied",
        project_root=project,
        mission_id="m",
        provider="codex",
        run_label="matcher",
        completed_at=1,
        status="denied",
    )
    assert missing.pricing_status == "partial" and missing.cost_usd is None
    assert unknown.pricing_status == "unpriced" and unknown.cost_usd is None
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append_many([missing, unknown, denied])
    summary = ledger.summary()
    assert summary.cost_usd is None
    assert summary.pricing_status == "partial"
    assert format_usage_cost(summary) == "partial"


def test_copilot_reconcile_prices_a_pending_turn_from_the_session_event_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn the CLI left no store row for is still settled from the session
    event log, so the daemon is not held on a phantom liability."""
    copilot_home = tmp_path / "copilot"
    copilot_home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(copilot_home))
    monkeypatch.setattr(
        "argus.core.usage._copilot_reconcile_enabled_for", lambda _root: True,
    )
    started_at = 1_790_751_193.42
    completed_at = 1_790_751_200.36
    events = copilot_home / "session-state" / "acp-1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps({
            "type": "session.start",
            "data": {"sessionId": "acp-1", "selectedModel": "gpt-5.6-sol"},
            "timestamp": "2026-09-30T06:53:14.415Z",
        })
        + "\n"
        + json.dumps({
            "type": "session.usage_checkpoint",
            "data": {"totalNanoAiu": 2_946_700_000, "totalPremiumRequests": 1},
            "timestamp": "2026-09-30T06:53:19.562Z",
        })
        + "\n",
        encoding="utf-8",
    )
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(
        replace(
            _record(
                call_id="acp-turn",
                project_root=project,
                mission_id=None,
                provider="copilot",
                run_label="manager-classify-fast",
                started_at=started_at,
                completed_at=completed_at,
                premium_requests=1.0,
                thread_id="acp-1",
                copilot_token_billing_expected=True,
            ),
            cost_usd=None,
            pricing_status="partial",
            pricing_tier="copilot_token_pending",
        )
    )
    pending = ledger.records()[0]
    assert pending.pricing_tier == "copilot_token_pending"

    assert ledger.ensure_copilot_usage_reconciled() == 1

    settled = ledger.records()[0]
    assert settled.cost_usd == pytest.approx(0.029467)
    assert settled.pricing_status == "priced"
    assert settled.pricing_tier == "copilot_token"
    assert settled.cost_basis == "token"
    assert settled.total_nano_aiu == 2_946_700_000
    assert settled.input_tokens is None
    assert settled.model_usage[0]["usage_event_id"] == 1
    # A second pass has nothing left to settle.
    assert ledger.ensure_copilot_usage_reconciled() == 0


# --- turns Argus itself interrupted before the Copilot CLI recorded them ---


def _interrupt_receipt(for_call: str, **overrides) -> dict:
    receipt = {
        "type": "agent.io.complete", "call_id": for_call,
        "run_label": "manager-supervision", "backend": "copilot", "model": "gpt-5.6-sol",
        "exit_code": -15, "thread_id": "acp-b4c1",
        "turn_completed": False, "turn_failed": True,
        "fatal_error": "External interrupt: Manager supervision superseded",
        "tool_activity_observed": False, "input_tokens": 0, "cached_input_tokens": 0,
        "cache_write_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0,
        "premium_requests": None, "premium_requests_present": False, "total_nano_aiu": None,
        "agent_message_count": 0, "agent_message_chars": 0, "stdout_line_count": 0,
        "json_event_count": 0,
    }
    receipt.update(overrides)
    return receipt


def test_interrupted_empty_copilot_turn_settles_as_one_request_when_recorded() -> None:
    from argus.core.pricing import copilot_usd_per_premium_request
    from argus.core.usage import INTERRUPTED_REQUEST_TIER

    record = _record(
        call_id="superseded",
        project_root=Path("/tmp/p1"),
        mission_id=None,
        provider="copilot",
        run_label="manager-supervision",
        status="error",
        error="External interrupt: Manager supervision superseded",
        copilot_token_billing_expected=True,
        thread_id="acp-b4c1",
        startup_receipt=_interrupt_receipt("superseded"),
    )
    assert record.pricing_status == "priced"
    assert record.pricing_tier == INTERRUPTED_REQUEST_TIER
    assert record.cost_basis == "premium_request"
    assert record.cost_usd == pytest.approx(copilot_usd_per_premium_request())
    assert record.premium_request_cost_usd == pytest.approx(copilot_usd_per_premium_request())
    assert record.premium_requests is None


def test_copilot_reconcile_settles_a_pending_interrupted_turn_from_its_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows written before this rule existed are settled by reconciliation."""
    from argus.core.pricing import copilot_usd_per_premium_request
    from argus.core.usage import INTERRUPTED_REQUEST_TIER

    copilot_home = tmp_path / "copilot"
    copilot_home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(copilot_home))
    monkeypatch.setattr(
        "argus.core.usage._copilot_reconcile_enabled_for", lambda _root: True,
    )
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    (project / "events.jsonl").write_text(
        json.dumps(_interrupt_receipt("superseded")) + "\n", encoding="utf-8",
    )
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(
        replace(
            _record(
                call_id="superseded",
                project_root=project,
                mission_id=None,
                provider="copilot",
                run_label="manager-supervision",
                status="error",
                error="External interrupt: Manager supervision superseded",
                started_at=1_790_755_911.2,
                completed_at=1_790_755_913.2,
                copilot_token_billing_expected=True,
                thread_id="acp-b4c1",
            ),
            cost_usd=None,
            pricing_status="partial",
            pricing_tier="copilot_token_pending",
            cost_basis="none",
        )
    )
    pending = ledger.records()[0]
    assert pending.pricing_status == "partial"

    assert ledger.ensure_copilot_usage_reconciled() == 1
    settled = ledger.records()[0]
    assert settled.pricing_status == "priced"
    assert settled.pricing_tier == INTERRUPTED_REQUEST_TIER
    assert settled.cost_usd == pytest.approx(copilot_usd_per_premium_request())
    assert ledger.ensure_copilot_usage_reconciled() == 0

    # If the CLI does record the session later, the exact charge replaces the
    # request estimate.
    events = copilot_home / "session-state" / "acp-b4c1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps({
            "type": "session.start",
            "data": {"sessionId": "acp-b4c1", "selectedModel": "gpt-5.6-sol"},
            "timestamp": "2026-09-30T08:11:51.300Z",
        })
        + "\n"
        + json.dumps({
            "type": "session.usage_checkpoint",
            "data": {"totalNanoAiu": 1_100_000_000, "totalPremiumRequests": 1},
            "timestamp": "2026-09-30T08:11:53.100Z",
        })
        + "\n",
        encoding="utf-8",
    )
    assert ledger.ensure_copilot_usage_reconciled() == 1
    exact = ledger.records()[0]
    assert exact.pricing_tier == "copilot_token"
    assert exact.cost_basis == "token"
    assert exact.cost_usd == pytest.approx(0.011)
