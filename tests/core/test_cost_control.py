from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from argus.core import cost_control
from argus.core.cost_control import (
    COST_CONTROL_AUDIT_FILE,
    COST_CONTROL_STATE_FILE,
    _locked,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, build_usage_record


def _usage() -> TokenUsage:
    return TokenUsage(
        input_tokens=1_000,
        output_tokens=100,
        input_tokens_present=True,
        output_tokens_present=True,
        source="test",
    )


def _record(
    project: Path, call_id: str, *, model: str = "gpt-5.6-sol", provider: str = "codex",
):
    return build_usage_record(
        call_id=call_id,
        project_root=project,
        mission_id="mission-1",
        provider=provider,
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
    lock_timeout_seconds: float = 0.25,
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
        lock_timeout_seconds=lock_timeout_seconds,
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


def test_admission_does_not_wait_for_busy_housekeeping_lock(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
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
        started = time.monotonic()
        reservation, reason = _reserve(
            tmp_path,
            project,
            "call-during-contention",
            lock_timeout_seconds=0.02,
        )
        elapsed = time.monotonic() - started

        assert reservation is not None and reason == ""
        assert reservation.state_tracked is False
        assert elapsed < 0.2

        record = _record(project, reservation.call_id)
        UsageLedger(project, migrate_legacy=False).append(record)
        assert reservation.settle(record) is True
    finally:
        release.set()
        holder.join(timeout=1)


def test_settlement_does_not_delay_result_behind_busy_housekeeping_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = _reserve(tmp_path, project, "tracked-call")
    assert reservation is not None and reason == ""
    assert reservation.state_tracked is True

    record = _record(project, reservation.call_id)
    UsageLedger(project, migrate_legacy=False).append(record)
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _locked(tmp_path):
            entered.set()
            release.wait(timeout=2)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=1)
    observed_timeouts: list[float | None] = []

    def observed_lock(root: Path, *, timeout_seconds: float | None = None):
        if root == tmp_path:
            observed_timeouts.append(timeout_seconds)
        return _locked(root, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(cost_control, "_locked", observed_lock)
    try:
        assert reservation.settle(record) is True
        # Keep the real contention, but do not include unrelated Windows I/O
        # and scheduling overhead in a sub-second wall-clock assertion.
        assert observed_timeouts == [0.25]
        assert holder.is_alive(), "settlement must finish before housekeeping unlocks"
    finally:
        release.set()
        holder.join(timeout=1)

    # The durable usage row lets the next read prune the deferred reservation.
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 0


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


@pytest.mark.parametrize("provider", ["codex", "pi"])
def test_call_with_unpriceable_model_is_counted_and_never_refused(
    tmp_path: Path,
    provider: str,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "call-unknown")
    assert reservation is not None
    record = _record(project, "call-unknown", model="future-model", provider=provider)
    assert record.pricing_status == "unpriced"
    UsageLedger(project, migrate_legacy=False).append(record)
    reservation.settle(record)

    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
    assert snapshot["unresolved"][0]["provider"] == provider
    assert "no configured price for model future-model" in snapshot["unresolved"][0]["reason"]
    assert snapshot["daily_tokens"] > 0
    assert UsageLedger(project, migrate_legacy=False).records()[0].cost_usd is None

    # Nothing will ever settle a model without a price; it is counted at the
    # day's costliest priced call (nothing is priced yet here) and work goes on,
    # on that model or any other.
    for model in ("future-model", "", "gpt-5.6-sol"):
        again, reason = reserve_call_budget(
            call_id=f"again-{model or 'default'}",
            project_root=project,
            mission_id="manager-turn",
            provider=provider,
            model=model,
            run_label="manager-frontdoor-classify",
            global_root=tmp_path,
            global_daily_cap_usd=10.0,
        )
        assert again is not None and reason == ""
        again.release(reason="test")


def test_unknown_future_state_version_is_not_silently_rewritten(tmp_path: Path) -> None:
    state = cost_control._default_state(time.time())
    state["version"] = 999
    path = tmp_path / COST_CONTROL_STATE_FILE
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(cost_control.CostControlStateError, match="unsupported"):
        cost_control_snapshot(global_root=tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("daily_cap", [10.0, 0.000001])
def test_admission_reconciles_known_token_cost_before_deciding_the_budget(
    tmp_path: Path, monkeypatch, daily_cap: float,
) -> None:
    from argus.core.pricing import MODEL_PRICES_USD_PER_MTOK

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    model = "test-newly-priced-model"
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "pending-price")
    assert reservation is not None
    record = _record(project, "pending-price", model=model)
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(record)
    reservation.settle(record)

    admitted, reason = _reserve(tmp_path, project, "before-pricing")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")
    unresolved = cost_control_snapshot(global_root=tmp_path)["unresolved"][0]
    assert unresolved["provider"] == "codex"
    assert unresolved["model"] == model
    assert "no configured price" in unresolved["reason"]
    monkeypatch.setitem(
        MODEL_PRICES_USD_PER_MTOK, model, MODEL_PRICES_USD_PER_MTOK["gpt-5.5"],
    )

    admitted, reason = _reserve(
        tmp_path, project, "after-pricing", global_daily_cap_usd=daily_cap,
    )
    resolved = ledger.records()[0]
    assert resolved.cost_usd is not None and resolved.cost_usd > 0
    assert resolved.pricing_status == "priced"
    if daily_cap > resolved.cost_usd:
        assert admitted is not None and reason == ""
        assert json.loads((tmp_path / COST_CONTROL_STATE_FILE).read_text())["unresolved"] == []
        admitted.release(reason="test")
    else:
        assert admitted is None
        assert "global daily budget exhausted" in reason


@pytest.mark.parametrize(
    "error",
    [
        "",
        "External interrupt: operator abort requested: stop now",
    ],
)
def test_partial_copilot_cost_does_not_block_new_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
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
        started_at=time.time() - 1,
        completed_at=time.time(),
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
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["unresolved_calls"] == 1
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


def test_snapshot_falls_back_to_atomic_read_on_busy_global_lock(tmp_path: Path) -> None:
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
        snapshot = cost_control_snapshot(
            global_root=tmp_path,
            lock_timeout_seconds=0.02,
        )
        assert snapshot["snapshot_stale"] is True
        assert snapshot["active_reservations"] == 0
        assert snapshot["unresolved_calls"] == 0
    finally:
        release.set()
        holder.join(timeout=1)


def test_corrupt_global_cost_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / COST_CONTROL_STATE_FILE).write_text("{bad", encoding="utf-8")
    reservation, reason = _reserve(tmp_path, None, "call-1")
    assert reservation is None
    assert "cost control unavailable" in reason


def test_snapshot_reports_todays_premium_requests_and_cost_by_run_label(tmp_path: Path, monkeypatch) -> None:
    # A request-billed backend can spend real money while recording zero
    # tokens; the snapshot must say what today's requests cost and for what.
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "0.04")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    now = time.time()
    ledger = UsageLedger(project, migrate_legacy=False)
    for call_id, label, requests in (("a", "engineer", 3.0), ("b", "engineer", 1.0), ("c", "planner", 1.0)):
        ledger.append(build_usage_record(
            call_id=call_id, project_root=project, mission_id="m", provider="copilot",
            model="any-model", run_label=label, started_at=now - 1, completed_at=now,
            status="completed", premium_requests=requests,
        ))
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["daily_premium_requests"] == 5.0
    assert snapshot["daily_premium_usd"] == pytest.approx(0.20)
    by_label = {row["run_label"]: row for row in snapshot["premium_by_run_label"]}
    assert by_label["engineer"]["premium_requests"] == 4.0
    assert by_label["engineer"]["calls"] == 2
    assert by_label["planner"]["usd"] == pytest.approx(0.04)
    assert snapshot["premium_by_run_label"][0]["run_label"] == "engineer"  # costliest first
