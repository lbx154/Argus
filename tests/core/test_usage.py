from __future__ import annotations

import gc
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argus_skill.core.token_usage import TokenUsage, extract_token_usage
from argus_skill.core.usage import (
    UsageLedger,
    UsageRecord,
    build_usage_record,
    ensure_project_events_standardized,
    format_usage_cost,
    project_usage_summary,
    usage_recorded_event,
)
from argus_skill.life.supervisor import global_daily_spend
from argus_skill.life.supervisor._cost import _CostTrackingSink
from argus_skill.webapi.server import _settled_spend


class _Sink:
    def handle_event(self, event: dict) -> None:  # noqa: ARG002
        return None


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


def test_usage_process_caches_are_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.core import usage

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


def test_real_copilot_fixture_preserves_matcher_and_scientist_output_tokens(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    matcher = extract_token_usage(fixture["matcher"]["json_events"])
    scientist = extract_token_usage(fixture["scientist"]["json_events"])
    assert matcher.output_tokens == 118
    assert scientist.output_tokens == 13_175

    project = tmp_path / "projects" / "s-fixture"
    project.mkdir(parents=True)
    rows = [
        {"type": "life.mission.started", "item_id": "mission-1", "ts": 1.0},
        fixture["matcher"],
        fixture["scientist"],
        {
            "type": "life.mission.completed",
            "item_id": "mission-1",
            "cost_usd": 0.0,
            "ts": 1783757314.0,
        },
    ]
    (project / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    records = UsageLedger(project).records(mission_id="mission-1")
    assert len(records) == 2
    assert [record.output_tokens for record in records] == [118, 13_175]
    assert project_usage_summary(
        project,
        mission_id="mission-1",
    ).output_tokens == 13_293


def test_usage_ledger_is_idempotent_by_call_id(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    record = build_usage_record(
        call_id="call-1",
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        token_usage=_known_usage(input_tokens=1000, output_tokens=200),
    )
    assert ledger.append(record) is True
    assert ledger.append(record) is False
    assert ledger.summary().call_count == 1
    assert len((project / "usage.jsonl").read_text().splitlines()) == 1


def test_pending_tokens_are_reconciled_and_persisted_when_pricing_becomes_available(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.core.pricing import MODEL_PRICES_USD_PER_MTOK

    model = "test-newly-priced-model"
    project = tmp_path / "project"
    ledger = UsageLedger(project, migrate_legacy=False)
    record = build_usage_record(
        call_id="pending-price",
        project_root=project,
        mission_id=None,
        provider="codex",
        model=model,
        run_label="manager-frontdoor-classify",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        token_usage=_known_usage(input_tokens=1000, output_tokens=200),
    )
    ledger.append(record)
    assert ledger.records()[0].cost_usd is None
    monkeypatch.setitem(
        MODEL_PRICES_USD_PER_MTOK, model, MODEL_PRICES_USD_PER_MTOK["gpt-5.5"],
    )

    resolved = ledger.records()[0]
    assert resolved.pricing_status == "priced"
    assert resolved.cost_usd is not None and resolved.cost_usd > 0
    persisted = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert persisted["cost_usd"] == resolved.cost_usd
    assert persisted["call_id"] == record.call_id
    settled_bytes = ledger.path.read_bytes()

    monkeypatch.setitem(
        MODEL_PRICES_USD_PER_MTOK, model, MODEL_PRICES_USD_PER_MTOK["gpt-5.6-sol"],
    )
    assert UsageLedger(project, migrate_legacy=False).records()[0].cost_usd == resolved.cost_usd
    assert ledger.path.read_bytes() == settled_bytes


@pytest.mark.parametrize(
    "provider,model,usage,extra",
    [
        ("codex", "unknown-model", _known_usage(input_tokens=100, output_tokens=20), {}),
        ("codex", "", _known_usage(input_tokens=100, output_tokens=20), {}),
        ("codex", "gpt-5.5", TokenUsage(input_tokens=100, input_tokens_present=True), {}),
        ("copilot", "gpt-5.5", _known_usage(input_tokens=100, output_tokens=20),
         {"copilot_token_billing_expected": True}),
        ("opencode", "gpt-5.5", _known_usage(input_tokens=100, output_tokens=20),
         {"provider_cost_usd": 0.0123}),
    ],
)
def test_token_reconciliation_does_not_guess_or_overwrite_provider_billing(
    tmp_path: Path, provider: str, model: str, usage: TokenUsage, extra: dict,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    record = build_usage_record(
        call_id="unchanged",
        project_root=tmp_path,
        mission_id=None,
        provider=provider,
        model=model,
        run_label="manager",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        token_usage=usage,
        **extra,
    )
    ledger.append(record)
    original = ledger.path.read_bytes()

    assert ledger.records() == [record]
    assert ledger.path.read_bytes() == original


def test_usage_reconciliation_uses_a_process_lock_without_fcntl(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.core import usage

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
    completed = build_usage_record(
        call_id="late-provider",
        project_root=tmp_path,
        mission_id=None,
        provider="opencode",
        model="gpt-5.5",
        run_label="manager",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
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


def test_opencode_provider_reported_cost_is_authoritative(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    record = build_usage_record(
        call_id="opencode-call",
        project_root=project,
        mission_id="mission-1",
        provider="opencode",
        model="openai/gpt-5.4",
        run_label="engineer-r1",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        token_usage=_known_usage(input_tokens=1000, output_tokens=200),
        provider_cost_usd=0.0123,
    )

    assert record.pricing_status == "priced"
    assert record.pricing_tier == "provider_reported"
    assert record.cost_basis == "provider_reported"
    assert record.cost_usd == pytest.approx(0.0123)


def test_stale_copilot_resume_error_is_read_as_not_billed() -> None:
    record = UsageRecord.from_jsonable({
        "call_id": "stale-resume",
        "project_id": "s-1",
        "provider": "copilot",
        "status": "error",
        "pricing_status": "partial",
        "pricing_tier": "premium_request_only",
        "cost_usd": None,
        "model_usage": [],
        "total_nano_aiu": None,
        "error": "Error: No session, task, or name matched 'old-thread'.",
    })

    assert record.pricing_status == "not_billed"
    assert record.pricing_tier == "not_started"
    assert record.cost_usd == 0.0


def test_daemon_stop_before_provider_start_is_read_as_not_billed() -> None:
    record = UsageRecord.from_jsonable({
        "call_id": "stopped-before-start",
        "project_id": "s-1",
        "provider": "copilot",
        "status": "error",
        "pricing_status": "partial",
        "pricing_tier": "premium_request_only",
        "cost_usd": None,
        "model_usage": [],
        "total_nano_aiu": None,
        "error": "refused before start: daemon stop requested",
    })

    assert record.pricing_status == "not_billed"
    assert record.cost_usd == 0.0


@pytest.mark.parametrize(
    "provider,error",
    [
        ("copilot", "copilot wrapper: real Copilot CLI binary not found"),
        ("copilot", "Error: No authentication information found."),
        ("opencode", "Error: Token refresh failed: 401"),
    ],
)
def test_local_backend_refusal_is_read_as_not_billed(
    provider: str,
    error: str,
) -> None:
    record = UsageRecord.from_jsonable({
        "call_id": f"{provider}-pre-provider",
        "project_id": "s-1",
        "provider": provider,
        "status": "error",
        "pricing_status": "partial",
        "pricing_tier": "unknown",
        "cost_usd": None,
        "model_usage": [],
        "total_nano_aiu": None,
        "error": error,
    })

    assert record.pricing_status == "not_billed"
    assert record.pricing_tier == "not_started"
    assert record.cost_usd == 0.0


def test_stale_resume_error_with_observed_premium_usage_stays_partial() -> None:
    record = UsageRecord.from_jsonable({
        "call_id": "billed-resume",
        "project_id": "s-1",
        "provider": "copilot",
        "status": "error",
        "pricing_status": "partial",
        "pricing_tier": "premium_request_only",
        "cost_usd": None,
        "premium_requests": 1.0,
        "model_usage": [],
        "total_nano_aiu": None,
        "error": "Error: No session, task, or name matched 'old-thread'.",
    })

    assert record.pricing_status == "partial"
    assert record.cost_usd is None


@pytest.mark.parametrize("error", [
    "Error: Access denied by policy settings. Your Copilot CLI policy is disabled.",
    "Your Copilot subscription does not include this feature",
    "Required policies have not been enabled for Copilot CLI",
    "Error: Failed to load models (Request ID: request-1)",
    "Copilot could not retrieve the list of available models.",
])
def test_copilot_startup_policy_refusal_is_unbilled_for_new_and_existing_records(
    tmp_path: Path, error: str
) -> None:
    record = build_usage_record(
        call_id="policy-startup", project_root=tmp_path / "p1", mission_id=None,
        provider="copilot", model="gpt-5.6-sol", run_label="reviewer",
        started_at=1, completed_at=2, status="error", error=error,
        copilot_token_billing_expected=True,
    )
    assert record.pricing_status == "not_billed"
    assert record.cost_usd == 0.0
    # Re-reading a pre-fix persisted row must also release its unresolved cost.
    old = record.to_jsonable()
    old.update(cost_usd=None, pricing_status="partial", pricing_tier="copilot_token_pending")
    repaired = UsageRecord.from_jsonable(old)
    assert repaired.pricing_status == "not_billed"
    assert repaired.cost_usd == 0.0


@pytest.mark.parametrize("evidence", ["tokens", "aiu", "premium", "provider_cost"])
@pytest.mark.parametrize("error", [
    "Error: Access denied by policy settings",
    "Error: Failed to load models (Request ID: request-1)",
])
def test_policy_error_after_metered_work_preserves_billing(
    tmp_path: Path, evidence: str, error: str
) -> None:
    kwargs = {
        "tokens": {"token_usage": _known_usage(input_tokens=100, output_tokens=20)},
        "aiu": {"total_nano_aiu": 432_724_659_000},
        "premium": {"premium_requests": 1.0},
        "provider_cost": {"provider_cost_usd": 1.25},
    }[evidence]
    record = build_usage_record(
        call_id="policy-after-work", project_root=tmp_path / "p1", mission_id=None,
        provider="opencode" if evidence == "provider_cost" else "copilot",
        model="gpt-5.6-sol", run_label="reviewer", started_at=1, completed_at=2,
        status="error", error=error,
        **kwargs,
    )
    assert record.pricing_status != "not_billed"
    assert record.cost_usd is None or record.cost_usd > 0
    reread = UsageRecord.from_jsonable(record.to_jsonable())
    assert reread.pricing_status == record.pricing_status
    assert reread.cost_usd == record.cost_usd


def test_usage_recorded_event_v2_is_self_contained(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    record = build_usage_record(
        call_id="call-1",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="simple-1",
        started_at=10.0,
        completed_at=11.25,
        status="completed",
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

    event = usage_recorded_event(record)

    assert event["schema_version"] == 2
    assert event["project_id"] == "p1"
    assert event["thread_id"] == "thread-1"
    assert event["duration_ms"] == 1250
    assert event["usage"]["input_tokens"] == event["input_tokens"] == 100
    assert event["usage"]["models"][0]["model"] == "gpt-5.6-sol"
    assert event["pricing"]["cost_basis"] == event["cost_basis"] == "token"
    assert event["pricing"]["cost_usd"] == pytest.approx(0.02)

    ledger = UsageLedger(project, migrate_legacy=False)
    assert ledger.append(record) is True
    stored = ledger.records()[0]
    assert stored.thread_id == "thread-1"
    assert stored.duration_ms == 1250
    assert stored.model_usage[0]["total_nano_aiu"] == 2_000_000_000
    assert stored.model_usage[0]["usage_event_id"] == 7
    assert stored.model_usage[0]["session_id"] == "thread-1"


def test_copilot_premium_request_quote_settles_without_token_price(
    tmp_path: Path,
) -> None:
    record = build_usage_record(
        call_id="premium-only",
        project_root=tmp_path / "p1",
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager-frontdoor-classify",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        premium_requests=1.0,
    )

    assert record.pricing_status == "priced"
    assert record.pricing_tier == "premium_request"
    assert record.cost_basis == "premium_request"
    assert record.cost_usd == pytest.approx(0.04)
    assert record.premium_request_cost_usd == pytest.approx(0.04)


def test_copilot_missing_premium_and_token_prices_remains_partial(
    tmp_path: Path,
) -> None:
    record = build_usage_record(
        call_id="unknown-copilot",
        project_root=tmp_path / "p1",
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="planner",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
    )

    assert record.pricing_status == "partial"
    assert record.cost_basis == "none"
    assert record.cost_usd is None


@pytest.mark.parametrize(
    ("status", "error", "premium_requests", "expected_status"),
    [
        ("completed", "", 1.0, "partial"),
        ("denied", "provider cooldown", None, "not_billed"),
        ("error", "Error: No session, task, or name matched 'stale-thread'.", None, "not_billed"),
    ],
)
def test_modern_token_billing_distinguishes_missing_usage_from_refusals(
    tmp_path: Path, status, error, premium_requests, expected_status
) -> None:
    record = build_usage_record(
        call_id="modern", project_root=tmp_path / "p1", mission_id=None,
        provider="copilot", model="gpt-5.6-sol", run_label="engineer-r1",
        started_at=1.0, completed_at=2.0, status=status, error=error,
        premium_requests=premium_requests, copilot_token_billing_expected=True,
    )
    assert record.pricing_status == expected_status
    assert record.cost_usd == (0.0 if expected_status == "not_billed" else None)


def test_non_copilot_still_requires_token_pricing(tmp_path: Path) -> None:
    record = build_usage_record(
        call_id="unknown-codex",
        project_root=tmp_path / "p1",
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="planner",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        premium_requests=1.0,
    )

    assert record.pricing_status == "partial"
    assert record.cost_basis == "token"
    assert record.cost_usd is None


def test_copilot_token_cost_takes_precedence_without_double_charging(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    record = build_usage_record(
        call_id="token-and-premium",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager-frontdoor-classify",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        premium_requests=1.0,
        total_nano_aiu=2_000_000_000,
    )
    ledger.append(record)

    assert record.cost_basis == "token"
    assert record.cost_usd == pytest.approx(0.02)
    assert record.premium_request_cost_usd == pytest.approx(0.04)
    assert ledger.summary().known_cost_usd == pytest.approx(0.02)


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
        ledger.append(build_usage_record(
            call_id=call_id,
            project_root=project,
            mission_id="mission-1",
            provider="copilot",
            model="gpt-5.6-sol",
            run_label=call_id,
            started_at=1.0,
            completed_at=2.0,
            status="completed",
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


def test_legacy_worktree_events_migrate_once_without_call_duplicates(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    legacy = project / ".argus" / "events.jsonl"
    legacy.parent.mkdir(parents=True)
    canonical_start = {
        "type": "agent.io.start",
        "call_id": "call-1",
        "ts": 1.0,
    }
    project.mkdir(parents=True, exist_ok=True)
    (project / "events.jsonl").write_text(
        json.dumps(canonical_start) + "\n",
        encoding="utf-8",
    )
    legacy_rows = [
        {**canonical_start, "ts": 2.0},
        {"type": "agent.io.complete", "call_id": "call-1", "ts": 3.0},
        {
            "type": "agent.io.stream",
            "call_id": "call-1",
            "line": "first",
            "ts": 2.1,
        },
        {
            "type": "agent.io.stream",
            "call_id": "call-1",
            "line": "second",
            "ts": 2.2,
        },
    ]
    legacy.write_text(
        "".join(json.dumps(row) + "\n" for row in legacy_rows) + "not-json\n",
        encoding="utf-8",
    )

    assert ensure_project_events_standardized(project) == 3
    assert ensure_project_events_standardized(project) == 0

    rows = [
        json.loads(line)
        for line in (project / "events.jsonl").read_text().splitlines()
    ]
    assert sum(row["type"] == "agent.io.start" for row in rows) == 1
    assert sum(row["type"] == "agent.io.complete" for row in rows) == 1
    assert [
        row["line"] for row in rows if row["type"] == "agent.io.stream"
    ] == ["first", "second"]
    marker = json.loads(
        (project / "events.migration-v2.json").read_text(encoding="utf-8")
    )
    assert marker["rows_appended"] == 3
    assert marker["duplicate_rows"] == 1
    assert marker["malformed_rows"] == 1


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
    event_dir = project / ".argus"
    event_dir.mkdir()
    (event_dir / "events.jsonl").write_text(
        json.dumps({
            "type": "agent.io.complete",
            "call_id": "call-1",
            "thread_id": "session-1",
        })
        + "\n",
        encoding="utf-8",
    )

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

    second = build_usage_record(
        call_id="denied",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
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


def test_copilot_reconcile_does_not_reprice_settled_premium_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.core.usage._copilot_reconcile_enabled_for",
        lambda _project_root: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.usage.find_copilot_usage_near",
        lambda **_kwargs: None,
    )
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.04")
    ledger.append(build_usage_record(
        call_id="old-rate",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager-frontdoor-classify",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        premium_requests=1.0,
    ))
    assert ledger.ensure_copilot_usage_reconciled() == 0

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.10")
    ledger.append(build_usage_record(
        call_id="new-rate",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="planner",
        started_at=3.0,
        completed_at=4.0,
        status="completed",
        premium_requests=1.0,
    ))

    assert ledger.ensure_copilot_usage_reconciled() == 0
    costs = {
        record.call_id: record.cost_usd
        for record in ledger.records()
    }
    assert costs == {
        "old-rate": pytest.approx(0.04),
        "new-rate": pytest.approx(0.10),
    }


def test_partial_token_charge_never_falls_back_to_a_premium_request_estimate(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.core.usage._copilot_reconcile_enabled_for", lambda _root: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.usage.find_copilot_usage_near", lambda **_kwargs: None,
    )
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    record = build_usage_record(
        call_id="partial-token-charge",
        project_root=tmp_path,
        mission_id=None,
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        premium_requests=1.0,
        total_nano_aiu=17,
        thread_id="session-1",
    )
    ledger.append(replace(record, cost_usd=None, pricing_status="partial"))

    ledger.ensure_copilot_usage_reconciled()

    pending = ledger.records()[0]
    assert pending.cost_usd is None
    assert pending.pricing_status == "partial"
    assert pending.pricing_tier == "copilot_token_pending"


def test_legacy_migration_preserves_unknown_resumed_premium_delta(
    tmp_path: Path,
) -> None:
    project = tmp_path / "legacy"
    project.mkdir()
    rows = [
        {"type": "life.mission.started", "item_id": "mission-1", "ts": 1.0},
        {
            "type": "agent.io.complete",
            "call_id": "resumed-call",
            "backend": "copilot",
            "model": "gpt-5.6-sol",
            "run_label": "manager-frontdoor-classify",
            "thread_id": "resumed-session",
            "premium_requests": None,
            "premium_requests_present": False,
            "json_events": [{
                "type": "result",
                "usage": {"premiumRequests": 15.0},
            }],
            "ts": 2.0,
        },
    ]
    (project / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    records = UsageLedger(project).records()

    assert len(records) == 1
    assert records[0].premium_requests is None
    assert records[0].pricing_status == "partial"
    assert records[0].cost_usd is None


def test_legacy_codex_migration_uses_recorded_call_deltas_not_raw_cumulative(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    rows = [
        {"type": "life.mission.started", "item_id": "mission-1", "ts": 1.0},
        {
            "type": "agent.io.complete",
            "call_id": "call-1",
            "run_label": "engineer-r1",
            "backend": "codex",
            "model": "gpt-5.6-sol",
            "thread_id": "thread-1",
            "input_tokens": 100,
            "output_tokens": 20,
            "ts": 2.0,
            "json_events": [
                {"input_tokens": 100, "output_tokens": 20},
            ],
        },
        {
            "type": "agent.io.complete",
            "call_id": "call-2",
            "run_label": "engineer-r2",
            "backend": "codex",
            "model": "gpt-5.6-sol",
            "thread_id": "thread-1",
            "input_tokens": 50,
            "output_tokens": 10,
            "ts": 3.0,
            "json_events": [
                {"input_tokens": 150, "output_tokens": 30},
            ],
        },
    ]
    (project / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary = UsageLedger(project).summary(mission_id="mission-1")
    assert summary.call_count == 2
    assert summary.input_tokens == 150
    assert summary.output_tokens == 30


def test_call_mission_project_and_daily_aggregates_match(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    now = time.time()
    records = [
        build_usage_record(
            call_id=f"call-{index}",
            project_root=project,
            mission_id="mission-1",
            provider="codex",
            model="gpt-5.6-sol",
            run_label=label,
            started_at=now - 1,
            completed_at=now,
            status="completed",
            token_usage=_known_usage(
                input_tokens=1000 * index,
                output_tokens=100 * index,
            ),
        )
        for index, label in ((1, "engineer-r1"), (2, "reviewer"))
    ]
    assert ledger.append_many(records) == 2
    call_sum = sum(record.cost_usd or 0.0 for record in records)

    sink = _CostTrackingSink(
        _Sink(),
        engineer_model="gpt-5.6-sol",
        reviewer_model="gpt-5.6-sol",
        usage_ledger=ledger,
        mission_id="mission-1",
    )
    mission_sum = sink.total_usd()
    project_sum = _settled_spend(None, project).known_cost_usd
    daily_sum = global_daily_spend(global_root=root)

    assert mission_sum == pytest.approx(call_sum)
    assert project_sum == pytest.approx(call_sum)
    assert daily_sum == pytest.approx(call_sum)


def test_completed_call_counts_after_mission_is_killed_before_completion_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    now = time.time()
    record = build_usage_record(
        call_id="completed-before-kill",
        project_root=project,
        mission_id="mission-killed",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=now - 1,
        completed_at=now,
        status="completed",
        token_usage=_known_usage(input_tokens=10_000, output_tokens=2_000),
    )
    ledger.append(record)
    (project / "events.jsonl").write_text(
        json.dumps({
            "type": "life.mission.started",
            "item_id": "mission-killed",
            "ts": now - 2,
        })
        + "\n",
        encoding="utf-8",
    )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(
        record.cost_usd
    )
    assert project_usage_summary(
        project,
        mission_id="mission-killed",
    ).call_count == 1


def test_missing_usage_and_unknown_model_are_never_rendered_as_zero(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p"
    missing = build_usage_record(
        call_id="missing",
        project_root=project,
        mission_id="m",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=1,
        completed_at=2,
        status="error",
        token_usage=TokenUsage(),
    )
    unknown = build_usage_record(
        call_id="unknown",
        project_root=project,
        mission_id="m",
        provider="codex",
        model="future-model",
        run_label="reviewer",
        started_at=1,
        completed_at=2,
        status="completed",
        token_usage=_known_usage(input_tokens=100, output_tokens=20),
    )
    denied = build_usage_record(
        call_id="denied",
        project_root=project,
        mission_id="m",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="matcher",
        started_at=1,
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
