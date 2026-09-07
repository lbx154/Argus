from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.models import RunnerOptions
from argus_skill.core.usage import UsageLedger, build_usage_record
from argus_skill.provider_integrations import copilot_usage
from argus_skill.provider_integrations.copilot_usage import (
    NANO_AIU_PER_USD,
    capture_copilot_usage_cursor,
    copilot_store_supports_token_billing,
    find_copilot_usage_near,
    read_copilot_usage_since,
)


def _db(home: Path) -> Path:
    path = home / "session-store.db"
    home.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
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
    return path


def _insert(path: Path, *, session: str, model: str, created_at: str, **usage) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO assistant_usage_events (
                session_id, turn_index, model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, reasoning_tokens,
                total_nano_aiu, request_multiplier, created_at
            ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, 1.0, ?)
            """,
            (
                session,
                model,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("cache_read_tokens"),
                usage.get("cache_write_tokens"),
                usage.get("reasoning_tokens"),
                usage.get("total_nano_aiu"),
                created_at,
            ),
        )


def test_reads_exact_rows_added_after_cursor(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    _insert(
        path,
        session="old",
        model="gpt-5.6-sol",
        created_at="2026-07-11T09:00:00Z",
        input_tokens=1,
        output_tokens=1,
        total_nano_aiu=1,
    )
    cursor = capture_copilot_usage_cursor()
    assert cursor is not None and cursor.max_id == 1

    _insert(
        path,
        session="session-1",
        model="gpt-5.6-sol",
        created_at="2026-07-11T10:00:00Z",
        input_tokens=12_935,
        output_tokens=5,
        cache_read_tokens=200,
        cache_write_tokens=300,
        reasoning_tokens=7,
        total_nano_aiu=8_099_000_000,
    )
    usage = read_copilot_usage_since(cursor, session_id="session-1")
    assert usage is not None
    assert usage.model == "gpt-5.6-sol"
    assert usage.model_usage[0]["usage_event_id"] == 2
    assert usage.model_usage[0]["session_id"] == "session-1"
    assert usage.input_tokens == 12_935
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 200
    assert usage.cache_write_tokens == 300
    assert usage.reasoning_tokens == 7
    assert usage.cost_usd == pytest.approx(0.08099)


def test_read_waits_for_delayed_usage_row_even_when_store_initially_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    cursor = capture_copilot_usage_cursor()
    assert cursor is not None and cursor.max_id == 0

    inserted = threading.Event()

    def insert_later() -> None:
        time.sleep(0.1)
        _insert(
            path,
            session="session-1",
            model="gpt-5.6-sol",
            created_at="2026-07-11T10:00:00Z",
            input_tokens=20,
            output_tokens=3,
            cache_read_tokens=10,
            total_nano_aiu=2_000_000_000,
        )
        inserted.set()

    thread = threading.Thread(target=insert_later)
    thread.start()
    try:
        usage = read_copilot_usage_since(cursor, session_id="session-1", timeout=1.0)
    finally:
        thread.join(timeout=1.0)

    assert inserted.is_set()
    assert usage is not None
    assert usage.model == "gpt-5.6-sol"
    assert usage.input_tokens == 20
    assert usage.cache_read_tokens == 10
    assert usage.output_tokens == 3
    assert usage.cost_usd == pytest.approx(0.02)


@pytest.mark.parametrize("nano_aiu", [2_000_000_000, 0, None])
def test_read_waits_for_existing_partial_row_to_receive_billing(
    tmp_path: Path, monkeypatch, nano_aiu: int | None
) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    cursor = capture_copilot_usage_cursor()
    _insert(
        path, session="session-1", model="gpt-5.6-sol",
        created_at="2026-07-11T10:00:00Z", input_tokens=20, output_tokens=3,
    )
    polls = 0

    def advance(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 2:
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "UPDATE assistant_usage_events SET total_nano_aiu = ?",
                    (nano_aiu,),
                )

    monkeypatch.setattr(
        copilot_usage, "time",
        SimpleNamespace(monotonic=lambda: polls * 0.05, sleep=advance),
    )
    usage = read_copilot_usage_since(cursor, session_id="session-1", timeout=0.5)
    assert usage is not None
    assert usage.total_nano_aiu == nano_aiu
    if nano_aiu is None:
        assert polls * 0.05 >= 0.5
        assert usage.cost_usd is None
    else:
        assert usage.cost_usd == pytest.approx(nano_aiu / NANO_AIU_PER_USD)


def test_cursor_survives_store_created_by_new_copilot_process(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "new-copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(home))
    cursor = capture_copilot_usage_cursor()
    assert cursor is not None and cursor.max_id == 0
    path = _db(home)
    _insert(
        path,
        session="session-1",
        model="gpt-5.6-sol",
        created_at="2026-07-11T10:00:00Z",
        input_tokens=10,
        output_tokens=2,
        total_nano_aiu=1_000_000_000,
    )
    usage = read_copilot_usage_since(cursor, session_id="session-1")
    assert usage is not None and usage.cost_usd == pytest.approx(0.01)


def test_sums_multiple_model_calls_in_one_run(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    cursor = capture_copilot_usage_cursor()
    assert cursor is not None
    for model, tokens, nano in (
        ("gpt-5.6-sol", 100, 2_000_000_000),
        ("claude-haiku-4.5", 50, 1_000_000_000),
    ):
        _insert(
            path,
            session="session-1",
            model=model,
            created_at="2026-07-11T10:00:00Z",
            input_tokens=tokens,
            output_tokens=2,
            total_nano_aiu=nano,
        )
    usage = read_copilot_usage_since(cursor, session_id="session-1")
    assert usage is not None
    assert usage.model == "mixed"
    assert usage.input_tokens == 150
    assert usage.output_tokens == 4
    assert usage.total_nano_aiu == 3_000_000_000
    assert usage.cost_usd == pytest.approx(0.03)
    assert [row["model"] for row in usage.model_usage] == [
        "gpt-5.6-sol",
        "claude-haiku-4.5",
    ]
    assert usage.model_usage[0]["input_tokens"] == 100
    assert usage.model_usage[0]["usage_event_id"] == 1
    assert usage.model_usage[0]["session_id"] == "session-1"
    assert usage.model_usage[0]["cost_usd"] == pytest.approx(0.02)
    assert usage.model_usage[1]["usage_event_id"] == 2
    assert usage.model_usage[1]["cost_usd"] == pytest.approx(0.01)


def test_finds_historical_usage_by_session_and_time(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    _insert(
        path,
        session="session-1",
        model="gpt-5.6-sol",
        created_at="2026-07-11T10:00:02Z",
        input_tokens=10,
        output_tokens=2,
        total_nano_aiu=NANO_AIU_PER_USD,
    )
    found = find_copilot_usage_near(
        completed_at=1_783_764_003.0,
        started_at=1_783_764_000.0,
        session_id="session-1",
    )
    assert found is not None
    _, usage = found
    assert usage.cost_usd == pytest.approx(1.0)


@pytest.mark.parametrize("created_at", [
    "2026-07-11T10:00:02.000Z",
    "2026-07-11 10:00:02",
    "2026-07-11T03:00:02-07:00",
    "2026-07-11T12:00:02+02:00",
    "2026-07-11T09:59:59.500Z",
])
def test_historical_lookup_compares_instants_not_timestamp_text(
    tmp_path: Path, monkeypatch, created_at: str
) -> None:
    home = tmp_path / "copilot"
    path = _db(home)
    monkeypatch.setattr(copilot_usage, "copilot_usage_db_candidates", lambda: [path])
    _insert(
        path, session="session-1", model="gpt-5.6-sol", created_at=created_at,
        total_nano_aiu=NANO_AIU_PER_USD,
    )
    for session, timestamp in [
        ("unrelated", created_at),
        ("session-1", "2026-07-11T03:01:00-07:00"),
        ("session-1", "2026-07-11 09:59:00"),
    ]:
        _insert(
            path, session=session, model="gpt-5.6-sol", created_at=timestamp,
            total_nano_aiu=99 * NANO_AIU_PER_USD,
        )

    found = find_copilot_usage_near(
        started_at=1_783_764_000.0, completed_at=1_783_764_003.0,
        session_id="session-1",
    )
    assert found is not None
    _, usage = found
    assert len(usage.rows) == 1
    assert usage.cost_usd == pytest.approx(1.0)


def test_historical_lookup_never_attributes_usage_without_a_session(
    tmp_path: Path, monkeypatch
) -> None:
    path = _db(tmp_path / "copilot")
    monkeypatch.setattr(copilot_usage, "copilot_usage_db_candidates", lambda: [path])
    _insert(
        path, session="unrelated", model="gpt-5.6-sol",
        created_at="2026-07-11T10:00:02Z", total_nano_aiu=NANO_AIU_PER_USD,
    )
    assert find_copilot_usage_near(
        started_at=1_783_764_000.0, completed_at=1_783_764_003.0,
    ) is None


@pytest.mark.parametrize("store_exists", [False, True])
def test_default_cursor_uses_argus_home_with_existing_personal_database(
    tmp_path: Path, monkeypatch, store_exists: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "operator"))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus"))
    personal = _db(tmp_path / "operator" / ".copilot")
    _insert(
        personal, session="unrelated", model="gpt-5.6-sol",
        created_at="2026-09-06T06:00:00Z", total_nano_aiu=99 * NANO_AIU_PER_USD,
    )
    home = tmp_path / "argus" / "copilot-home"
    if store_exists:
        _db(home)

    cursor = capture_copilot_usage_cursor()
    assert cursor is not None
    assert cursor.db_path == home / "session-store.db"
    assert cursor.max_id == 0
    if not store_exists:
        # Accounting must neither prepare/copy configs nor select personal DB
        # just because the actual child has not created its home yet.
        assert not home.exists()
        _db(home)
    _insert(
        home / "session-store.db", session="argus-call", model="gpt-5.6-sol",
        created_at="2026-09-06T06:10:00Z", input_tokens=3_381_755,
        output_tokens=23_981, total_nano_aiu=432_724_659_000,
    )

    usage = read_copilot_usage_since(cursor, session_id="argus-call", timeout=0)
    assert usage is not None
    assert usage.input_tokens == 3_381_755
    assert usage.cost_usd == pytest.approx(4.32724659)


def test_personal_fallback_uses_its_own_baseline_and_exact_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "operator"))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "unavailable-argus"))
    personal = _db(tmp_path / "operator" / ".copilot")
    _insert(
        personal, session="resumed", model="gpt-5.6-sol",
        created_at="2026-09-06T06:00:00Z", total_nano_aiu=99 * NANO_AIU_PER_USD,
    )
    cursor = capture_copilot_usage_cursor()
    _insert(
        personal, session="another-call", model="gpt-5.6-sol",
        created_at="2026-09-06T06:10:00Z", total_nano_aiu=88 * NANO_AIU_PER_USD,
    )
    _insert(
        personal, session="resumed", model="gpt-5.6-sol",
        created_at="2026-09-06T06:10:00Z", total_nano_aiu=NANO_AIU_PER_USD,
    )
    usage = read_copilot_usage_since(cursor, session_id="resumed", timeout=0)
    assert usage is not None
    assert len(usage.rows) == 1
    assert usage.cost_usd == pytest.approx(1.0)


@pytest.mark.parametrize("store_exists", [False, True])
def test_modern_missing_usage_is_pending_and_reconciles_late_wal_write(
    tmp_path: Path, monkeypatch, store_exists: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "operator"))
    root = tmp_path / "argus"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    # Reproduce the actual two-store layout, with no COPILOT_HOME in the parent.
    _db(tmp_path / "operator" / ".copilot")
    home = root / "copilot-home"
    db = home / "session-store.db"
    if store_exists:
        _db(home)
    project = root / "projects" / "p1"
    backend = AgentCliBackend(backend="copilot")
    backend.set_usage_context(project_root=project, mission_id="mission-1")

    def fake_run_exec(_runner, **_kwargs):
        return AgentRunResult(
            command=["copilot"], exit_code=0, thread_id="late-session",
            turn_completed=True, agent_messages=["done"],
            json_events=[{"type": "result", "usage": {"premiumRequests": 1.0}}],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec)
    result = backend.run_exec(
        prompt="bounded work", run_label="engineer-r1",
        options=RunnerOptions(model="gpt-5.6-sol", working_dir=str(tmp_path)),
    )
    assert result.pricing_status == "partial"
    assert result.cost_usd is None
    ledger = UsageLedger(project)

    def unexpected_event_scan(_root):
        pytest.fail("modern ledger thread_id must not require raw event history")

    monkeypatch.setattr("argus_skill.core.usage._legacy_call_threads", unexpected_event_scan)
    if not store_exists:
        # A first invocation with a store that has not appeared yet cannot be
        # declared legacy. Preserve pending status until the late DB arrives.
        first = ledger.records()[0]
        assert first.pricing_tier == "copilot_token_pending"
        assert first.cost_usd is None
        assert not db.exists()
        _db(home)
    # Keep a connection open so the delayed row changes only the WAL, not the DB.
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        first = ledger.records()[0]
        assert first.pricing_tier == "copilot_token_pending"
        assert first.premium_request_cost_usd == pytest.approx(0.04)
        ledger_before = ledger.path.read_bytes()
        db_before = (db.stat().st_size, db.stat().st_mtime_ns)
        timestamp = datetime.fromtimestamp(
            (first.started_at + first.completed_at) / 2, UTC
        ).isoformat().replace("+00:00", "Z")
        _insert(
            db, session="late-session", model="gpt-5.6-sol", created_at=timestamp,
            input_tokens=5_831_150, output_tokens=48_622, cache_read_tokens=5_492_779,
            total_nano_aiu=486_116_960_000,
        )
        assert ledger.path.read_bytes() == ledger_before
        assert (db.stat().st_size, db.stat().st_mtime_ns) == db_before
        settled = ledger.records()[0]
        assert settled.pricing_status == "priced"
        assert settled.cost_usd == pytest.approx(4.8611696)
        assert settled.input_tokens == 5_831_150
        assert settled.premium_request_cost_usd == pytest.approx(0.04)
        assert len(settled.model_usage) == 1
        assert ledger.ensure_copilot_usage_reconciled() == 0


def test_partial_model_cost_is_not_summed_as_a_complete_charge(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "copilot"
    db = _db(home)
    monkeypatch.setenv("COPILOT_HOME", str(home))
    cursor = capture_copilot_usage_cursor()
    for model, nano in (("gpt-5.6-sol", NANO_AIU_PER_USD), ("gpt-5.6-terra", None)):
        _insert(
            db, session="session-1", model=model, created_at="2026-09-06T06:00:00Z",
            input_tokens=100, output_tokens=20, total_nano_aiu=nano,
        )
    usage = read_copilot_usage_since(cursor, session_id="session-1", timeout=0)
    assert usage is not None
    assert usage.input_tokens == 200
    assert usage.total_nano_aiu is None
    assert usage.cost_usd is None
    timestamp = datetime(2026, 9, 6, 6, tzinfo=UTC).timestamp()
    ledger = UsageLedger(tmp_path / "project")
    ledger.append(build_usage_record(
        call_id="mixed", project_root=ledger.project_root, mission_id=None,
        provider="copilot", model="mixed", run_label="engineer-r1",
        started_at=timestamp - 1, completed_at=timestamp + 1, status="completed",
        premium_requests=1.0, thread_id="session-1", model_usage=usage.model_usage,
        copilot_token_billing_expected=True,
    ))
    pending = ledger.records()[0]
    assert pending.pricing_status == "partial"
    assert pending.cost_usd is None
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE assistant_usage_events SET total_nano_aiu = ? WHERE model = ?",
            (2 * NANO_AIU_PER_USD, "gpt-5.6-terra"),
        )
    settled = ledger.records()[0]
    assert settled.pricing_status == "priced"
    assert settled.cost_usd == pytest.approx(3.0)
    assert len(settled.model_usage) == 2
    assert ledger.summary().cost_usd == pytest.approx(3.0)


def test_legacy_premium_only_cli_without_token_store_still_settles(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "legacy-copilot"
    home.mkdir()
    with sqlite3.connect(home / "session-store.db") as connection:
        connection.execute(
            "CREATE TABLE assistant_usage_events "
            "(id INTEGER PRIMARY KEY, session_id TEXT, premium_requests REAL)"
        )
    monkeypatch.setenv("COPILOT_HOME", str(home))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    backend = AgentCliBackend(backend="copilot")
    project = tmp_path / "project"
    backend.set_usage_context(project_root=project, mission_id=None)

    def fake_run_exec(_runner, **_kwargs):
        return AgentRunResult(
            command=["copilot"], exit_code=0, thread_id="legacy-session",
            turn_completed=True, agent_messages=["done"],
            json_events=[{"type": "result", "usage": {"premiumRequests": 1.0}}],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec)
    result = backend.run_exec(
        prompt="reply", run_label="manager-frontdoor-classify",
        options=RunnerOptions(model="gpt-5.6-sol", working_dir=str(tmp_path)),
    )
    assert result.pricing_status == "priced"
    assert result.cost_usd == pytest.approx(0.04)
    assert UsageLedger(project).records()[0].cost_basis == "premium_request"


@pytest.mark.parametrize("state", ["missing", "uninitialized", "unreadable"])
def test_unknown_store_does_not_establish_premium_only_billing(tmp_path: Path, state: str) -> None:
    db = tmp_path / "session-store.db"
    if state == "unreadable":
        db.write_bytes(b"incomplete SQLite file")
    elif state == "uninitialized":
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE sessions (id TEXT)")
    assert copilot_store_supports_token_billing(db)
