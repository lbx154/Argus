from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from argus_skill.core import cost_control
from argus_skill.core.cost_control import (
    _locked,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus_skill.core.token_usage import TokenUsage
from argus_skill.core.usage import UsageLedger, build_usage_record


@pytest.fixture(autouse=True)
def _budget_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1000")


def _reserve(root: Path, project: Path, call_id: str, **kwargs):
    project.mkdir(parents=True, exist_ok=True)
    return reserve_call_budget(
        call_id=call_id,
        project_root=project,
        mission_id="mission",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=root,
        **kwargs,
    )


def _record(project: Path, call_id: str, *, cost: float | None, **kwargs):
    record = build_usage_record(
        call_id=call_id,
        project_root=project,
        mission_id="mission",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=time.time() - 1,
        completed_at=time.time(),
        status="completed",
        token_usage=TokenUsage(
            input_tokens=100,
            output_tokens=10,
            input_tokens_present=True,
            output_tokens_present=True,
            source="test",
        ),
    )
    return replace(record, cost_usd=cost, **kwargs)


def test_concurrent_observed_spend_reaches_shared_cap(tmp_path: Path) -> None:
    first, reason = _reserve(tmp_path, tmp_path / "projects" / "p1", "first")
    second, second_reason = _reserve(tmp_path, tmp_path / "projects" / "p2", "second")
    assert first is not None and reason == ""
    assert second is not None and second_reason == ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].observe_cost(pair[1]), [(first, 600), (second, 400)]))

    assert results.count("") == 1
    assert sum("global daily budget exhausted" in result for result in results) == 1
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 2
    assert snapshot["in_flight_cost_usd"] == 1000
    assert first.amount_usd == second.amount_usd == 0

    # An old cumulative observation cannot refund already observed spend.
    assert "global daily budget exhausted" in first.observe_cost(100)
    assert cost_control_snapshot(global_root=tmp_path)["in_flight_cost_usd"] == 1000
    denied, reason = _reserve(tmp_path, tmp_path / "projects" / "p3", "third")
    assert denied is None
    assert "global daily budget exhausted" in reason


def test_settled_call_replaces_its_live_cost_once(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    first, _ = _reserve(tmp_path, project, "first")
    second, _ = _reserve(tmp_path, project, "second")
    assert first is not None and second is not None
    assert first.observe_cost(600) == ""
    UsageLedger(project, migrate_legacy=False).append(_record(project, "first", cost=600))

    # Settlement may be durable before the reservation's finalizer runs.
    assert second.observe_cost(399) == ""
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 1
    assert snapshot["in_flight_cost_usd"] == 399
    assert "($0.000000 available)" in second.observe_cost(400)


def test_settled_copilot_events_are_deduplicated_across_calls(tmp_path: Path) -> None:
    event = {"session_id": "shared-session", "usage_event_id": 1, "cost_usd": 600}
    for name in ("p1", "p2"):
        project = tmp_path / "projects" / name
        UsageLedger(project, migrate_legacy=False).append(
            _record(project, name, cost=600, provider="copilot", model_usage=(event,))
        )

    reservation, reason = _reserve(tmp_path, tmp_path / "projects" / "p3", "third")

    assert reservation is not None and reason == ""
    assert reservation.observe_cost(399) == ""
    assert "($0.000000 available)" in reservation.observe_cost(400)


def test_midnight_discards_previous_day_observed_cost(tmp_path: Path) -> None:
    local = time.localtime()
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    reservation, reason = _reserve(
        tmp_path, tmp_path / "projects" / "p1", "overnight", now=midnight - 60
    )
    assert reservation is not None and reason == ""
    assert reservation.observe_cost(900, now=midnight - 30) == ""

    assert reservation.observe_cost(0, now=midnight + 1) == ""
    snapshot = cost_control_snapshot(global_root=tmp_path, now=midnight + 1)
    assert snapshot["active_reservations"] == 1
    assert snapshot["in_flight_cost_usd"] == 0
    assert reservation.observe_cost(200, now=midnight + 30) == ""
    assert cost_control_snapshot(global_root=tmp_path, now=midnight + 30)["in_flight_cost_usd"] == 200


def test_unpriced_durable_usage_is_nonblocking_and_reconciled(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    unknown = _record(
        project, "unknown", cost=None, pricing_status="unpriced", model="unlisted-model",
    )
    ledger.append(unknown)

    admitted, reason = _reserve(tmp_path, project, "allowed")
    assert admitted is not None and reason == ""
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    assert ledger.summary().cost_usd is None
    assert ledger.summary().known_cost_usd == 0
    admitted.release(reason="test")

    # Simulate durable late provider reconciliation of this exact call.
    ledger.path.write_text(
        json.dumps(replace(unknown, cost_usd=25, pricing_status="priced").to_jsonable()) + "\n",
        encoding="utf-8",
    )
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0
    assert ledger.summary().cost_usd == 25
    admitted, reason = _reserve(tmp_path, project, "after-reconciliation")
    assert admitted is not None and reason == ""


def test_unknown_settlement_resolves_when_priced_ledger_arrives(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    reservation, _ = _reserve(tmp_path, project, "unknown")
    assert reservation is not None
    reservation.settle_unknown(reason="provider interrupted before final usage")
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    admitted, reason = _reserve(tmp_path, project, "before-reconciliation")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")

    UsageLedger(project, migrate_legacy=False).append(_record(project, "unknown", cost=10))

    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0
    admitted, reason = _reserve(tmp_path, project, "after-reconciliation")
    assert admitted is not None and reason == ""


@pytest.mark.parametrize(
    ("pricing_status", "partial_cost"),
    [("unknown", None), ("partial", None), ("unpriced", None),
     ("partial", 4), ("unpriced", 4), ("partial", 12)],
)
@pytest.mark.parametrize("reconciled_cost", [6, 10, 12])
def test_uncertain_settlement_preserves_observed_floor_until_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    pricing_status: str, partial_cost: float | None, reconciled_cost: float,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    project = tmp_path / "projects" / "p1"
    reservation, _ = _reserve(tmp_path, project, "observed")
    assert reservation is not None
    assert "global daily budget exhausted" in reservation.observe_cost(10)
    denied, reason = _reserve(tmp_path, project, "before-finalization")
    assert denied is None and "global daily budget exhausted" in reason

    ledger = UsageLedger(project, migrate_legacy=False)
    pending = _record(
        project, "observed", cost=partial_cost,
        pricing_status=pricing_status, model="unlisted-model",
    )
    if pricing_status == "unknown":
        reservation.settle_unknown(reason="usage record was not persisted")
    else:
        ledger.append(pending)
        # A snapshot can run between the ledger write and finalization.
        snapshot = cost_control_snapshot(global_root=tmp_path)
        assert snapshot["in_flight_cost_usd"] == max(0, 10 - (partial_cost or 0))
        reservation.settle(pending)

    for _ in range(2):
        snapshot = cost_control_snapshot(global_root=tmp_path)
        assert snapshot["active_reservations"] == 1
        assert snapshot["in_flight_cost_usd"] == max(0, 10 - (partial_cost or 0))
        assert snapshot["unresolved_calls"] == 1
        assert snapshot["blocking_unresolved_calls"] == 0
        denied, reason = _reserve(tmp_path, project, "after-finalization")
        assert denied is None and "global daily budget exhausted" in reason

    # The partial ledger and original observation overlap, not add together.
    above_floor, reason = _reserve(
        tmp_path, project, "above-floor",
        global_daily_cap_usd=max(10, partial_cost or 0) + 1,
    )
    assert above_floor is not None and reason == ""
    above_floor.release(reason="test")
    state = json.loads((tmp_path / cost_control.COST_CONTROL_STATE_FILE).read_text())
    assert state["reservations"][0]["observed_cost_usd"] == 10
    if pricing_status != "unknown":
        assert ledger.records()[0].cost_usd == partial_cost
        assert ledger.records()[0].pricing_status == pricing_status

    ledger.path.write_text(
        json.dumps(replace(pending, cost_usd=reconciled_cost,
                           pricing_status="priced").to_jsonable()) + "\n",
        encoding="utf-8",
    )
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 0
    assert snapshot["in_flight_cost_usd"] == 0
    assert snapshot["unresolved_calls"] == 0
    assert ledger.summary().known_cost_usd == reconciled_cost
    after, reason = _reserve(tmp_path, project, "after-reconciliation")
    if reconciled_cost >= 10:
        assert after is None and "global daily budget exhausted" in reason
    else:
        assert after is not None and reason == ""
        after.release(reason="test")
    above_settled, reason = _reserve(
        tmp_path, project, "above-settled", global_daily_cap_usd=reconciled_cost + 1,
    )
    assert above_settled is not None and reason == ""
    above_settled.release(reason="test")


@pytest.mark.parametrize("observed_cost", [8, 10])
def test_partial_copilot_events_overlap_observed_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed_cost: float,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    project = tmp_path / "projects" / "p1"
    reservation, _ = _reserve(tmp_path, project, "observed")
    assert reservation is not None
    reservation.observe_cost(observed_cost)
    pending = build_usage_record(
        call_id="observed", project_root=project, mission_id="mission",
        provider="copilot", model="gpt-5.6-sol", run_label="engineer-r1",
        started_at=time.time() - 1, completed_at=time.time(), status="completed",
        model_usage=[
            {"session_id": "partial-session", "usage_event_id": 1,
             "model": "gpt-5.6-sol", "total_nano_aiu": 800_000_000_000},
            {"session_id": "partial-session", "usage_event_id": 2,
             "model": "unlisted-model", "input_tokens": 1, "output_tokens": 1},
        ],
    )
    assert pending.cost_usd is None and pending.pricing_status == "partial"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(pending)
    ledger.append(replace(pending, call_id="overlapping-events"))
    reservation.settle(pending)

    assert ledger.summary().known_cost_usd == 8
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["in_flight_cost_usd"] == observed_cost - 8
    assert snapshot["blocking_unresolved_calls"] == 0
    next_call, reason = _reserve(tmp_path, project, "next")
    if observed_cost < 10:
        assert next_call is not None and reason == ""
        next_call.release(reason="test")
    else:
        assert next_call is None and "global daily budget exhausted" in reason


def test_partial_known_cost_still_counts_toward_settled_and_live_caps(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(
        project, "partial", cost=600, pricing_status="partial", model="unlisted-model",
    ))
    assert ledger.summary().pricing_status == "partial"
    assert ledger.summary().known_cost_usd == 600

    admitted, reason = _reserve(tmp_path, project, "below-cap")
    assert admitted is not None and reason == ""
    assert admitted.observe_cost(399) == ""
    assert "global daily budget exhausted" in admitted.observe_cost(400)
    denied, reason = _reserve(tmp_path, project, "at-cap")
    assert denied is None and "global daily budget exhausted" in reason
    denied, reason = _reserve(tmp_path, project, "settled-cap", global_daily_cap_usd=600)
    assert denied is None and "global daily budget exhausted" in reason
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 1


def test_legacy_unresolved_flags_never_block_even_during_lock_contention(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    unknown, _ = _reserve(tmp_path, project, "legacy-unknown")
    assert unknown is not None
    unknown.settle_unknown(reason="provider usage unavailable")
    path = tmp_path / cost_control.COST_CONTROL_STATE_FILE
    state = json.loads(path.read_text())
    state["unresolved"][0]["blocking"] = True
    path.write_text(json.dumps(state), encoding="utf-8")

    with _locked(tmp_path):
        admitted, reason = _reserve(
            tmp_path, project, "during-contention", lock_timeout_seconds=0.01,
        )
        assert admitted is not None and reason == ""
        assert admitted.state_tracked is False
        snapshot = cost_control_snapshot(global_root=tmp_path, lock_timeout_seconds=0.01)
        assert snapshot["snapshot_stale"] is True
        assert snapshot["unresolved_calls"] == 1
        assert snapshot["blocking_unresolved_calls"] == 0
        assert snapshot["unresolved"][0]["blocking"] is False
    cost_control_snapshot(global_root=tmp_path)
    assert json.loads(path.read_text())["unresolved"][0]["blocking"] is False
    admitted.release(reason="test")


def test_observation_recovers_tracking_after_admission_lock_contention(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _locked(tmp_path):
            entered.set()
            release.wait(timeout=2)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=1)
    try:
        reservation, reason = _reserve(
            tmp_path, project, "contended", lock_timeout_seconds=0.01
        )
        assert reservation is not None and reason == ""
        assert reservation.state_tracked is False
    finally:
        release.set()
        holder.join(timeout=1)

    assert reservation.observe_cost(25) == ""
    reservation.settle_unknown(reason="final usage unavailable")

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 1
    assert snapshot["in_flight_cost_usd"] == 25
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0


def test_external_project_unknown_settlement_resolves_globally(tmp_path: Path) -> None:
    project = tmp_path / "external-project"
    reservation, _ = _reserve(tmp_path, project, "external-unknown")
    assert reservation is not None
    reservation.settle_unknown(reason="provider interrupted")
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 1

    UsageLedger(project, migrate_legacy=False).append(_record(project, "external-unknown", cost=10))

    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0
    admitted, reason = _reserve(tmp_path, tmp_path / "projects" / "p1", "other-project")
    assert admitted is not None and reason == ""


def test_dead_process_does_not_erase_observed_incurred_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    reservation, _ = _reserve(tmp_path, project, "crashed-after-usage")
    assert reservation is not None
    assert "global daily budget exhausted" in reservation.observe_cost(1000)

    monkeypatch.setattr(cost_control, "_pid_alive", lambda _pid: False)

    denied, reason = _reserve(tmp_path, project, "after-crash")
    assert denied is None
    assert "global daily budget exhausted" in reason
    assert cost_control_snapshot(global_root=tmp_path)["in_flight_cost_usd"] == 1000

    UsageLedger(project, migrate_legacy=False).append(
        _record(project, "crashed-after-usage", cost=1000)
    )
    assert cost_control_snapshot(global_root=tmp_path)["in_flight_cost_usd"] == 0


def test_external_project_spend_is_not_counted_twice(tmp_path: Path) -> None:
    project = tmp_path / "external-project"
    UsageLedger(project, migrate_legacy=False).append(_record(project, "settled", cost=600))
    first, reason = _reserve(tmp_path, project, "first")
    assert first is not None and reason == ""

    assert first.observe_cost(0) == ""
    second, reason = _reserve(tmp_path, project, "second")
    assert second is not None and reason == ""


def test_external_settled_spend_remains_known_after_reconciliation(tmp_path: Path) -> None:
    project = tmp_path / "external-project"
    reservation, _ = _reserve(tmp_path, project, "external-unknown")
    assert reservation is not None
    reservation.settle_unknown(reason="provider interrupted")
    UsageLedger(project, migrate_legacy=False).append(_record(project, "external-unknown", cost=1000))
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0

    denied, reason = _reserve(tmp_path, tmp_path / "projects" / "p1", "other-project")
    assert denied is None
    assert "global daily budget exhausted" in reason


@pytest.mark.parametrize("partial_total", [None, 17])
@pytest.mark.parametrize("legacy_marker", [False, True])
def test_admission_reconciles_late_copilot_sqlite_usage_without_ui_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    partial_total: int | None, legacy_marker: bool,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    pending = _record(
        project, "late-call", cost=None, provider="copilot", thread_id="late-session",
        pricing_status="partial", pricing_tier="copilot_token_pending",
        cost_basis="none", total_nano_aiu=partial_total,
    )
    ledger.append(pending)
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(home))
    with sqlite3.connect(home / "session-store.db") as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE assistant_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, turn_index INTEGER, model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                total_nano_aiu INTEGER, request_multiplier REAL, created_at TEXT
            )
        """)
        conn.commit()
        admitted, reason = _reserve(tmp_path, project, "before-usage")
        assert admitted is not None and reason == ""
        admitted.release(reason="test")
        if legacy_marker:
            marker = json.loads(ledger.copilot_reconcile_path.read_text(encoding="utf-8"))
            marker.update(version=4, pending_token_usage=False)
            ledger.copilot_reconcile_path.write_text(json.dumps(marker), encoding="utf-8")

        conn.execute(
            "INSERT INTO assistant_usage_events (session_id, turn_index, model, input_tokens, output_tokens, total_nano_aiu, created_at) VALUES (?, 0, ?, 100, 10, ?, ?)",
            ("late-session", "gpt-5.6-sol", 10 * 100_000_000_000,
             datetime.fromtimestamp(pending.completed_at, UTC).isoformat().replace("+00:00", "Z")),
        )
        conn.commit()

        admitted, reason = _reserve(tmp_path, project, "after-usage")

    assert admitted is not None and reason == ""
    settled = ledger.records()[0]
    assert settled.cost_usd == 10
    assert settled.pricing_status == "priced"
    assert cost_control_snapshot(global_root=tmp_path)["blocking_unresolved_calls"] == 0
