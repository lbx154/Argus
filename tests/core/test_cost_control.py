from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from argus_skill.core.codex_usage import TokenUsage
from argus_skill.core.cost_control import (
    COST_CONTROL_AUDIT_FILE,
    COST_CONTROL_STATE_FILE,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus_skill.core.usage import UsageLedger, build_usage_record


def _known_usage() -> TokenUsage:
    return TokenUsage(
        input_tokens=1_000,
        output_tokens=100,
        input_tokens_present=True,
        output_tokens_present=True,
        source="test",
    )


def _record(
    project: Path,
    call_id: str,
    *,
    model: str = "gpt-5.6-sol",
):
    return build_usage_record(
        call_id=call_id,
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model=model,
        run_label="engineer-r1",
        started_at=time.time() - 1,
        completed_at=time.time(),
        status="completed",
        token_usage=_known_usage(),
    )


def _reserve(root: Path, project: Path, call_id: str, **overrides):
    return reserve_call_budget(
        call_id=call_id,
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=root,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        **overrides,
    )


def test_atomic_reservation_blocks_concurrent_use_of_same_budget(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    first, reason = _reserve(tmp_path, project, "call-1")
    assert first is not None and reason == ""
    assert first.amount_usd == pytest.approx(10.0)

    blocked, reason = _reserve(tmp_path, project, "call-2")
    assert blocked is None
    assert "budget exhausted" in reason

    assert first.release(reason="test") is True
    assert first.release(reason="duplicate") is False
    second, reason = _reserve(tmp_path, project, "call-2")
    assert second is not None and reason == ""
    second.release(reason="test")


def test_priced_settlement_replaces_reservation_with_actual_ledger_cost(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "call-1")
    assert reservation is not None
    record = _record(project, "call-1")
    assert record.cost_usd is not None
    UsageLedger(project, migrate_legacy=False).append(record)

    assert reservation.settle(record) is True
    assert reservation.settle(record) is False
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 0
    assert snapshot["unresolved_calls"] == 0

    next_reservation, reason = _reserve(tmp_path, project, "call-2")
    assert next_reservation is not None and reason == ""
    assert next_reservation.amount_usd == pytest.approx(10.0 - record.cost_usd)
    next_reservation.release(reason="test")

    audit = [
        json.loads(line)
        for line in (tmp_path / COST_CONTROL_AUDIT_FILE).read_text().splitlines()
    ]
    assert {row["type"] for row in audit} >= {
        "budget.reservation.created",
        "budget.reservation.settled",
    }


def test_unpriced_settlement_blocks_until_usage_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "call-unknown")
    assert reservation is not None
    record = _record(project, "call-unknown", model="future-model")
    assert record.pricing_status == "unpriced" and record.cost_usd is None
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    blocked, reason = _reserve(tmp_path, project, "call-2")
    assert blocked is None
    assert "unresolved provider cost" in reason
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 1

    usage_path = project / "usage.jsonl"
    row = json.loads(usage_path.read_text().splitlines()[0])
    row.update({
        "pricing_status": "priced",
        "pricing_tier": "reconciled-test",
        "cost_basis": "token",
        "cost_usd": 0.5,
    })
    usage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    recovered, reason = _reserve(tmp_path, project, "call-2")
    assert recovered is not None and reason == ""
    assert cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0
    recovered.release(reason="test")


def test_unknown_settlement_blocks_and_corrupt_state_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "call-1")
    assert reservation is not None
    reservation.settle_unknown(reason="usage persistence failed")
    blocked, reason = _reserve(tmp_path, project, "call-2")
    assert blocked is None and "unresolved provider cost" in reason

    (tmp_path / COST_CONTROL_STATE_FILE).write_text("{broken", encoding="utf-8")
    blocked, reason = _reserve(tmp_path, project, "call-3")
    assert blocked is None
    assert "cost control unavailable" in reason


def test_dead_process_reservation_is_pruned(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    stale, _ = _reserve(tmp_path, project, "stale", pid=2_000_000_000)
    assert stale is not None

    live, reason = _reserve(tmp_path, project, "live")
    assert live is not None and reason == ""
    live.release(reason="test")


def test_threads_cannot_both_reserve_the_last_budget(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    barrier = threading.Barrier(2)
    results = []

    def worker(call_id: str) -> None:
        barrier.wait()
        results.append(_reserve(tmp_path, project, call_id))

    threads = [
        threading.Thread(target=worker, args=(f"call-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    allowed = [reservation for reservation, _reason in results if reservation]
    denied = [reason for reservation, reason in results if reservation is None]
    assert len(allowed) == 1
    assert len(denied) == 1 and "budget exhausted" in denied[0]
    allowed[0].release(reason="test")
