from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life import runtime_failure_circuit as circuit_module
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.runtime_failure_circuit import (
    CIRCUIT_FILENAME,
    active_runtime_failure_circuit,
    clear_runtime_failure_circuit,
    normalize_runtime_failure_message,
    record_runtime_failure_circuit,
    runtime_failure_fingerprint,
)
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_AWAITING
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState


def _checkpoint_error(mission_id: str) -> FileNotFoundError:
    try:
        raise FileNotFoundError(
            2,
            "No such file or directory",
            f"C:/Users/test/.argus-skill/projects/p/handoffs/{mission_id}/CHECKPOINT.md",
        )
    except FileNotFoundError as exc:
        return exc


def _identity(release: str = "release-a", source: str = "source-a") -> dict:
    return {
        "package_version": release,
        "source_revision": source,
        "relevant_source_digest": source,
        "checkpoint_contract_version": 2,
    }


def test_checkpoint_failure_fingerprint_ignores_per_mission_path() -> None:
    first = runtime_failure_fingerprint(_checkpoint_error("aaaaaaaaaaaa"))
    second = runtime_failure_fingerprint(_checkpoint_error("bbbbbbbbbbbb"))

    assert first["fingerprint"] == second["fingerprint"]
    assert first["callsite"] == second["callsite"]
    assert "<mission-id>/CHECKPOINT.md" in first["normalized_error"]
    assert "aaaaaaaaaaaa" not in first["normalized_error"]


def test_runtime_failure_circuit_persists_and_counts_same_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)

    first = record_runtime_failure_circuit(
        tmp_path,
        _checkpoint_error("aaaaaaaaaaaa"),
        item_id="item-1",
    )
    second = record_runtime_failure_circuit(
        tmp_path,
        _checkpoint_error("bbbbbbbbbbbb"),
        item_id="item-2",
    )

    assert first["newly_opened"] is True
    assert second["newly_opened"] is False
    assert second["occurrence_count"] == 2
    assert second["item_ids"] == ["item-1", "item-2"]
    active = active_runtime_failure_circuit(tmp_path)
    assert active is not None
    assert active["fingerprint"] == first["fingerprint"]


def test_release_or_relevant_source_change_closes_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", lambda: identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))

    identity = _identity(release="release-b", source="source-b")
    assert active_runtime_failure_circuit(tmp_path) is None
    persisted = json.loads((tmp_path / CIRCUIT_FILENAME).read_text(encoding="utf-8"))
    assert persisted["active"] is False
    assert persisted["cleared_reason"] == "runtime_identity_changed"


def test_reviewed_canary_can_explicitly_close_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    state = record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))

    assert clear_runtime_failure_circuit(
        tmp_path,
        reason="reviewed_canary_passed",
        fingerprint=state["fingerprint"],
    ) is True
    assert active_runtime_failure_circuit(tmp_path) is None


def test_open_runtime_circuit_holds_pending_mission_without_calling_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(BacklogItem.new(title="must wait", objective="do work"))
    calls = 0

    class _Runner:
        def execute(self, **_kwargs):  # pragma: no cover - proves circuit isolation
            nonlocal calls
            calls += 1
            raise AssertionError("runner must not execute while circuit is open")

    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=2),
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "infra_blocked"
    assert result["item_id"] == item.id
    assert calls == 0
    assert memory.backlog.next_pending() is not None
    blocked = [
        event
        for event in events
        if event.get("type") == "life.runtime_failure.circuit_blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0]["fingerprint"] == result["fingerprint"]


def test_planning_preflight_short_circuits_before_planner_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    memory = LifeMemory.open(tmp_path)
    memory.init()

    class _PlannerMustNotRun:
        def run_exec(self, **_kwargs):  # pragma: no cover - proves preflight gate
            raise AssertionError("Planner model must not run while circuit is open")

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(),
        planner_runner=_PlannerMustNotRun(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=2),
            continuous=True,
            continuous_objective="finish safely",
        ),
    )

    result = supervisor._pc_preflight_shortcircuits(_PlanCycleState(None))

    assert result == PLAN_AWAITING
    assert supervisor._suggested_sleep_s > 0


def test_supervisor_run_stops_cleanly_instead_of_spinning_on_open_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    memory = LifeMemory.open(tmp_path)
    memory.init()
    memory.backlog.add(BacklogItem.new(title="held", objective="held"))

    class _Runner:
        def execute(self, **_kwargs):  # pragma: no cover - proves no dispatch
            raise AssertionError("runner must not execute")

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    summary = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=2),
        ),
    ).run()

    assert summary["stopped_by"] == "infra_blocked"
    assert len(summary["results"]) == 1
    assert summary["suggested_sleep"] > 0


def test_reviewed_canary_item_bypasses_and_closes_open_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(
        BacklogItem.new(
            title="runtime canary",
            objective="prove the repaired settlement path",
            tags=["runtime_failure_canary"],
        )
    )

    class _CanaryRunner:
        def execute(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                status="done",
                final_message="canary passed",
                stop_reason="",
                rounds=1,
                final_review_status="done",
                final_review_source="reviewer",
                stage_transition={},
            )

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    supervisor = LifeSupervisor(
        memory=memory,
        runner=_CanaryRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=2),
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    row = next(row for row in memory.backlog.all() if row.id == item.id)
    assert row.status == "done"
    assert active_runtime_failure_circuit(tmp_path) is None


def test_uncaught_mission_exception_opens_circuit_before_second_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    memory = LifeMemory.open(tmp_path)
    memory.init()
    first = memory.backlog.add(BacklogItem.new(title="first", objective="first"))
    second = memory.backlog.add(BacklogItem.new(title="second", objective="second"))
    calls = 0

    class _CrashRunner:
        def execute(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise _checkpoint_error("cccccccccccc")

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    supervisor = LifeSupervisor(
        memory=memory,
        runner=_CrashRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=3),
        ),
    )

    failed = supervisor.tick()
    blocked = supervisor.tick()

    assert failed is not None and failed["status"] == "error"
    assert blocked is not None and blocked["status"] == "infra_blocked"
    assert calls == 1
    rows = {item.id: item for item in memory.backlog.all()}
    assert rows[first.id].status == "failed"
    assert rows[second.id].status == "pending"
    assert active_runtime_failure_circuit(tmp_path) is not None


def test_message_normalization_retains_exception_but_removes_runtime_id() -> None:
    normalized = normalize_runtime_failure_message(
        "FileNotFoundError: C:\\state\\handoffs\\7b3502dcd8b6\\CHECKPOINT.md"
    )
    assert normalized.startswith("FileNotFoundError:")
    assert "7b3502dcd8b6" not in normalized
    assert normalized.endswith("handoffs/<mission-id>/CHECKPOINT.md")
