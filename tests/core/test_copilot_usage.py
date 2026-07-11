from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from argus_skill.core.copilot_usage import (
    NANO_AIU_PER_USD,
    capture_copilot_usage_cursor,
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
    assert usage.input_tokens == 12_935
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 200
    assert usage.cache_write_tokens == 300
    assert usage.reasoning_tokens == 7
    assert usage.cost_usd == pytest.approx(0.08099)


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
    assert usage.model_usage[0]["cost_usd"] == pytest.approx(0.02)
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
