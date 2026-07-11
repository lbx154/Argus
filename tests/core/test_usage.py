from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from argus_skill.core.codex_usage import TokenUsage, extract_token_usage
from argus_skill.core.usage import (
    UsageLedger,
    UsageRecord,
    build_usage_record,
    format_usage_cost,
    project_usage_summary,
)
from argus_skill.life.supervisor import global_daily_spend
from argus_skill.life.supervisor._cost import _CostTrackingSink
from argus_skill.tools import dashboard
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


def test_call_mission_project_dashboard_and_daily_aggregates_match(
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
    _, dashboard_sum, dashboard_status = dashboard._missions_cost(
        [{"type": "life.mission.completed"}],
        project,
    )
    daily_sum = global_daily_spend(global_root=root)

    assert mission_sum == pytest.approx(call_sum)
    assert project_sum == pytest.approx(call_sum)
    assert dashboard_sum == pytest.approx(call_sum)
    assert dashboard_status == "priced"
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
