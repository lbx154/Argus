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
    CostControlStateError,
    _locked,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus_skill.core.usage import UsageLedger, build_usage_record


def _usage() -> TokenUsage:
    return TokenUsage(
        input_tokens=1_000,
        output_tokens=100,
        input_tokens_present=True,
        output_tokens_present=True,
        source="test",
    )


def _record(project: Path, call_id: str, *, model: str = "gpt-5.6-sol"):
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
        token_usage=_usage(),
    )


def _reserve(
    root: Path,
    project: Path | None,
    call_id: str,
    *,
    global_daily_cap_usd: float = 10.0,
    pid: int | None = None,
):
    return reserve_call_budget(
        call_id=call_id,
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=root,
        global_daily_cap_usd=global_daily_cap_usd,
        pid=pid,
    )


def test_calls_have_zero_dollar_admission_records(tmp_path: Path) -> None:
    reservation, reason = reserve_call_budget(
        call_id="global-only",
        project_root=None,
        mission_id=None,
        provider="copilot",
        model="gpt-5.5",
        run_label="engineer-r1",
        global_root=tmp_path,
        global_daily_cap_usd=1.25,
    )

    assert reason == ""
    assert reservation is not None
    assert reservation.amount_usd == 0.0
    reservation.release(reason="test")


def test_concurrent_projects_do_not_take_fixed_call_holds(tmp_path: Path) -> None:
    first_project = tmp_path / "projects" / "p1"
    second_project = tmp_path / "projects" / "p2"
    first_project.mkdir(parents=True)
    second_project.mkdir(parents=True)

    first, reason = _reserve(tmp_path, first_project, "call-1")
    assert first is not None and reason == ""
    second, reason = _reserve(tmp_path, second_project, "call-2")
    assert second is not None and reason == ""

    third, reason = _reserve(tmp_path, first_project, "call-3")
    assert third is not None and reason == ""
    assert first.amount_usd == second.amount_usd == third.amount_usd == 0.0

    first.release(reason="test")
    second.release(reason="test")
    third.release(reason="test")


def test_settled_global_spend_enforces_the_daily_cap(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    record = _record(project, "settled-call")
    assert record.cost_usd is not None
    UsageLedger(project, migrate_legacy=False).append(record)

    blocked, reason = _reserve(
        tmp_path,
        project,
        "next-call",
        global_daily_cap_usd=record.cost_usd,
    )

    assert blocked is None
    assert "global daily budget exhausted" in reason


def test_priced_settlement_replaces_hold_with_global_ledger_cost(
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
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 0
    assert snapshot["unresolved_calls"] == 0

    next_reservation, reason = _reserve(tmp_path, project, "call-2")
    assert next_reservation is not None and reason == ""
    assert next_reservation.amount_usd == 0.0
    next_reservation.release(reason="test")

    audit = [
        json.loads(line)
        for line in (tmp_path / COST_CONTROL_AUDIT_FILE).read_text().splitlines()
    ]
    assert {row["type"] for row in audit} >= {
        "budget.reservation.created",
        "budget.reservation.settled",
    }


def test_unpriced_cost_is_observed_without_globally_blocking(
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
    assert record.pricing_status == "unpriced"
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0

    next_call, reason = _reserve(tmp_path, project, "call-2")
    assert next_call is not None and reason == ""
    assert next_call.amount_usd == 0.0
    next_call.release(reason="test")

    control, reason = reserve_call_budget(
        call_id="control-1",
        project_root=project,
        mission_id="manager-turn",
        provider="copilot",
        model="gpt-5.5",
        run_label="manager-frontdoor-classify",
        global_root=tmp_path,
        global_daily_cap_usd=10.0,
    )
    assert control is not None and reason == ""
    control.release(reason="test")


@pytest.mark.parametrize(
    "error",
    [
        "",
        "External interrupt: operator abort requested: stop now",
    ],
)
def test_partial_copilot_cost_does_not_create_a_second_budget_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    admission, reason = reserve_call_budget(
        call_id="partial-copilot",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="planner",
        global_root=tmp_path,
        global_daily_cap_usd=10.0,
    )
    assert admission is not None and reason == ""
    record = build_usage_record(
        call_id="partial-copilot",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="planner",
        started_at=1.0,
        completed_at=2.0,
        status="completed",
        error=error,
    )
    assert record.pricing_status == "partial"
    UsageLedger(project, migrate_legacy=False).append(record)
    admission.settle(record)

    admitted, reason = reserve_call_budget(
        call_id="control-after-partial",
        project_root=project,
        mission_id="manager-turn",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager-frontdoor-classify",
        global_root=tmp_path,
        global_daily_cap_usd=10.0,
    )

    assert admitted is not None and reason == ""
    admitted.release(reason="test")


def test_dead_process_hold_is_pruned(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    stale, reason = _reserve(tmp_path, project, "stale", pid=999_999_999)
    assert stale is not None and reason == ""

    current, reason = _reserve(tmp_path, project, "current")
    assert current is not None and reason == ""
    assert current.amount_usd == 0.0
    current.release(reason="test")


def test_snapshot_times_out_on_busy_global_lock(tmp_path: Path) -> None:
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
        with pytest.raises(CostControlStateError, match="cost control lock busy"):
            cost_control_snapshot(
                global_root=tmp_path,
                lock_timeout_seconds=0.02,
            )
    finally:
        release.set()
        holder.join(timeout=1)


def test_corrupt_global_cost_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / COST_CONTROL_STATE_FILE).write_text("{bad", encoding="utf-8")
    reservation, reason = _reserve(tmp_path, None, "call-1")
    assert reservation is None
    assert "cost control unavailable" in reason
