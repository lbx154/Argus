from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
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

    control, reason = reserve_call_budget(
        call_id="control-1",
        project_root=project,
        mission_id="manager-turn",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="manager-frontdoor-classify",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=5.0,
    )
    assert control is not None and reason == ""
    assert control.amount_usd == pytest.approx(1.0)
    control.release(reason="test")

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


def test_interrupted_partial_cost_holds_reservation_without_global_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    monkeypatch.setattr(
        "argus_skill.core.usage._copilot_reconcile_enabled_for",
        lambda _project_root: False,
    )
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = reserve_call_budget(
        call_id="aborted",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=20.0,
        project_daily_cap_usd=20.0,
        global_daily_cap_usd=20.0,
        per_call_cap_usd=5.0,
    )
    assert reservation is not None and reason == ""
    record = build_usage_record(
        call_id="aborted",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        started_at=time.time() - 1,
        completed_at=time.time(),
        status="error",
        error="External interrupt: operator abort requested: stop now",
    )
    assert record.pricing_status == "partial" and record.cost_usd is None
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["blocking_unresolved_calls"] == 0
    assert snapshot["unresolved_held_usd"] == pytest.approx(5.0)
    assert snapshot["reserved_usd"] == pytest.approx(5.0)

    next_reservation, reason = reserve_call_budget(
        call_id="next",
        project_root=project,
        mission_id="mission-2",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="reviewer",
        global_root=tmp_path,
        per_mission_cap_usd=20.0,
        project_daily_cap_usd=20.0,
        global_daily_cap_usd=20.0,
        per_call_cap_usd=5.0,
    )
    assert next_reservation is not None
    assert reason == ""
    next_reservation.release(reason="test")


def test_interrupted_unknown_settlement_holds_exact_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = reserve_call_budget(
        call_id="aborted-unknown",
        project_root=project,
        mission_id="mission-1",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=20.0,
        project_daily_cap_usd=20.0,
        global_daily_cap_usd=20.0,
        per_call_cap_usd=4.25,
    )
    assert reservation is not None and reason == ""

    reservation.settle_unknown(
        reason="External interrupt: operator abort requested: stop"
    )

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["blocking_unresolved_calls"] == 0
    assert snapshot["unresolved_held_usd"] == pytest.approx(4.25)
    assert snapshot["unresolved"][0]["held_usd"] == pytest.approx(4.25)


def test_legacy_interrupt_without_recorded_hold_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_PER_CALL_CAP_USD", "0")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "legacy-abort")
    assert reservation is not None
    reservation.settle_unknown(
        reason="External interrupt: operator abort requested: old row"
    )
    state_path = tmp_path / COST_CONTROL_STATE_FILE
    state = json.loads(state_path.read_text())
    state["unresolved"][0].pop("blocking", None)
    state["unresolved"][0].pop("held_usd", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    blocked, reason = _reserve(tmp_path, project, "next")

    assert blocked is None
    assert "unresolved provider cost" in reason


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


def test_many_threads_share_global_budget_without_overselling(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    barrier = threading.Barrier(32)
    results = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        result = reserve_call_budget(
            call_id=f"stress-{index}",
            project_root=project,
            mission_id=f"mission-{index}",
            provider="codex",
            model="gpt-5.6-sol",
            run_label="engineer-r1",
            global_root=tmp_path,
            per_mission_cap_usd=10.0,
            project_daily_cap_usd=100.0,
            global_daily_cap_usd=10.0,
            per_call_cap_usd=1.0,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    allowed = [reservation for reservation, _reason in results if reservation]
    assert len(allowed) == 10
    assert sum(reservation.amount_usd for reservation in allowed) == pytest.approx(10.0)
    assert cost_control_snapshot(global_root=tmp_path)["reserved_usd"] == pytest.approx(10.0)
    for reservation in allowed:
        reservation.release(reason="test")


def test_concurrent_calls_respect_each_mission_cap_independently(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    barrier = threading.Barrier(6)
    results = []
    lock = threading.Lock()

    def worker(mission_id: str, index: int) -> None:
        barrier.wait()
        reservation, reason = reserve_call_budget(
            call_id=f"{mission_id}-{index}",
            project_root=project,
            mission_id=mission_id,
            provider="codex",
            model="gpt-5.6-sol",
            run_label="engineer-r1",
            global_root=tmp_path,
            per_mission_cap_usd=2.0,
            project_daily_cap_usd=100.0,
            global_daily_cap_usd=10.0,
            per_call_cap_usd=1.0,
        )
        with lock:
            results.append((mission_id, reservation, reason))

    threads = [
        threading.Thread(target=worker, args=(mission_id, index))
        for mission_id in ("m1", "m2")
        for index in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for mission_id in ("m1", "m2"):
        rows = [row for row in results if row[0] == mission_id]
        allowed = [reservation for _mid, reservation, _reason in rows if reservation]
        denied = [reason for _mid, reservation, reason in rows if reservation is None]
        assert len(allowed) == 2
        assert len(denied) == 1 and "mission budget exhausted" in denied[0]
        for reservation in allowed:
            reservation.release(reason="test")


def test_priced_fence_overrun_temporarily_blocks_only_that_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_FENCE_BREACH_POLICY", "block")
    monkeypatch.setenv("ARGUS_SKILL_FENCE_BREACH_COOLDOWN_S", "900")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = reserve_call_budget(
        call_id="overrun",
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
    )
    assert reservation is not None and reason == ""
    record = replace(_record(project, "overrun"), cost_usd=1.25)
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["fence_breach_calls"] == 1
    assert snapshot["fence_breaches"][0]["overrun_usd"] == pytest.approx(0.25)
    assert 0 < snapshot["fence_breach_remaining_seconds"] <= 900
    assert snapshot["fence_breach_next_recovery_at"] is not None

    blocked, reason = _reserve(tmp_path, project, "codex-after-breach")
    assert blocked is None
    assert "cooling down after budget fence breach" in reason

    other_project = tmp_path / "projects" / "p2"
    other_project.mkdir()
    isolated, reason = _reserve(
        tmp_path,
        other_project,
        "codex-other-project",
        per_call_cap_usd=1.0,
    )
    assert isolated is not None and reason == ""
    isolated.release(reason="test")

    state_path = tmp_path / COST_CONTROL_STATE_FILE
    state = json.loads(state_path.read_text())
    state["breaches"][0].pop("project_id")
    state_path.write_text(json.dumps(state))
    legacy_blocked, reason = _reserve(
        tmp_path,
        other_project,
        "codex-legacy-breach",
        per_call_cap_usd=1.0,
    )
    assert legacy_blocked is None
    assert "cooling down after budget fence breach" in reason

    copilot, reason = reserve_call_budget(
        call_id="copilot-still-allowed",
        project_root=project,
        mission_id="mission-2",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
    )
    assert copilot is not None and reason == ""
    copilot.release(reason="test")

    recovered, reason = reserve_call_budget(
        call_id="codex-after-cooldown",
        project_root=project,
        mission_id="mission-3",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
        now=time.time() + 901,
    )
    assert recovered is not None and reason == ""
    recovered.release(reason="test")
    assert cost_control_snapshot(
        global_root=tmp_path,
        now=time.time() + 901,
    )["fence_breach_remaining_seconds"] == 0

    audit = [
        json.loads(line)
        for line in (tmp_path / COST_CONTROL_AUDIT_FILE).read_text().splitlines()
    ]
    assert "budget.fence_breach.blocked" in {row["type"] for row in audit}


def test_control_plane_overrun_does_not_block_mission_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_FENCE_BREACH_POLICY", "block")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = reserve_call_budget(
        call_id="manager-overrun",
        project_root=project,
        mission_id="manager-turn",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="simple-1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=5.0,
    )
    assert reservation is not None and reason == ""
    assert reservation.amount_usd == pytest.approx(1.0)
    record = replace(
        _record(project, "manager-overrun"),
        provider="copilot",
        run_label="simple-1",
        cost_usd=1.25,
    )
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    blocked, reason = reserve_call_budget(
        call_id="manager-follow-up",
        project_root=project,
        mission_id="manager-turn-2",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="simple-1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
    )
    assert blocked is None
    assert "cooling down after budget fence breach" in reason

    mission, reason = reserve_call_budget(
        call_id="reviewer-still-allowed",
        project_root=project,
        mission_id="mission-2",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="reviewer",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
    )
    assert mission is not None and reason == ""
    mission.release(reason="test")
    breach = cost_control_snapshot(global_root=tmp_path)["fence_breaches"][0]
    assert breach["control_plane"] is True


def test_control_plane_call_cap_can_be_raised_by_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CONTROL_PLANE_CALL_CAP_USD", "4")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)

    reservation, reason = reserve_call_budget(
        call_id="raised-manager-cap",
        project_root=project,
        mission_id="manager-turn",
        provider="copilot",
        model="gpt-5.6-sol",
        run_label="simple-1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=5.0,
    )

    assert reservation is not None and reason == ""
    assert reservation.amount_usd == pytest.approx(4.0)
    reservation.release(reason="test")


def test_fence_breach_policy_can_explicitly_allow_follow_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_FENCE_BREACH_POLICY", "allow")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = reserve_call_budget(
        call_id="overrun",
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=tmp_path,
        per_mission_cap_usd=10.0,
        project_daily_cap_usd=100.0,
        global_daily_cap_usd=10.0,
        per_call_cap_usd=1.0,
    )
    assert reservation is not None
    record = replace(_record(project, "overrun"), cost_usd=1.25)
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    follow_up, reason = _reserve(tmp_path, project, "allowed-after-breach")
    assert follow_up is not None and reason == ""
    follow_up.release(reason="test")
