from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from argus_skill.life.memory import BacklogItem, IllegalStateTransition, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1


class _PauseThenCompleteRunner:
    def __init__(self) -> None:
        self.usage_mission_ids: list[str] = []

    def execute(self, **kwargs: Any) -> _Outcome:
        self.usage_mission_ids.append(str(kwargs["usage_mission_id"]))
        if len(self.usage_mission_ids) == 1:
            return _Outcome(
                success=False,
                status="paused_budget",
                stop_kind="budget_exhausted",
                recoverable=True,
                stop_reason="per-attempt cap reached",
            )
        return _Outcome(success=True, status="done")


class _CompleteRunner:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **_kwargs: Any) -> _Outcome:
        self.calls += 1
        return _Outcome(success=True, status="done")


class _ProviderFenceRunner:
    def __init__(self) -> None:
        self.usage_mission_ids: list[str] = []

    def execute(self, **kwargs: Any) -> _Outcome:
        self.usage_mission_ids.append(str(kwargs["usage_mission_id"]))
        return _Outcome(
            success=False,
            status="paused_provider_fence",
            stop_kind="provider_fence",
            recoverable=True,
            stop_reason="402 Payment Required: quota exhausted",
        )


def test_paused_budget_is_recoverable_with_fresh_usage_attempt(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    runner = _PauseThenCompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=3),
            poll_interval_seconds=0.01,
        ),
    )
    item = memory.backlog.add(
        BacklogItem.new(
            title="research",
            objective="continue from checkpoint",
            manager_decision={"routed": True, "vertical": "software"},
        )
    )

    paused = supervisor.tick()

    assert paused is not None
    assert paused["status"] == "paused_budget"
    assert paused["success"] is False
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_budget"
    assert stored.attempt == 1
    assert not [row for row in memory.backlog.all() if row.status == "failed"]
    with pytest.raises(IllegalStateTransition, match="resume_paused"):
        memory.backlog.update(item.id, status="pending")

    resumed = memory.backlog.resume_paused(item.id)
    assert resumed is not None
    assert resumed.status == "pending"
    assert resumed.attempt == 2

    completed = supervisor.tick()

    assert completed is not None and completed["success"] is True
    assert runner.usage_mission_ids == [
        f"{item.id}:attempt:1",
        f"{item.id}:attempt:2",
    ]


def test_budget_pause_backoff_never_starts_idle_timeout(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_PauseThenCompleteRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            continuous=True,
            continuous_objective="standing work",
        ),
    )

    supervisor._enter_pause_backoff()

    assert supervisor._idle_since is None
    assert supervisor._maybe_idle_timeout() == ""


@pytest.mark.parametrize(
    "status",
    [
        "paused_budget",
        "paused_provider_cooldown",
        "paused_daemon_shutdown",
    ],
)
def test_supervisor_auto_resumes_external_recoverable_pauses(
    tmp_path,
    status: str,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = BacklogItem.new(
        title="resume me",
        objective="continue",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = status
    memory.backlog.add(item)
    runner = _CompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
            poll_interval_seconds=0.0,
        ),
    )

    summary = supervisor.run()

    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "done"
    assert stored.attempt == 2
    assert runner.calls == 1
    assert summary["missions_run"] == 1


@pytest.mark.parametrize("continuous", [False, True])
def test_provider_fence_waits_across_passes_until_explicit_resume(
    tmp_path,
    monkeypatch,
    continuous: bool,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(
        title="quota held",
        objective="continue from checkpoint",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    runner = _ProviderFenceRunner()
    config = LifeSupervisorConfig(
        poll_interval_seconds=0.0,
        continuous=continuous,
        continuous_objective="standing work" if continuous else "",
    )

    def no_planning(*_args, **_kwargs):
        pytest.fail("a provider fence must not trigger another paid planning call")

    monkeypatch.setattr(LifeSupervisor, "_plan_next_work", no_planning)
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=_Sink(), config=config,
    )

    first = supervisor.run()
    assert first["stopped_by"] == "paused_provider_fence"
    assert first["missions_run"] == 1
    assert memory.backlog.all()[0].status == "paused_provider_fence"
    for _ in range(3):
        held = supervisor.run()
        assert held["stopped_by"] == "paused_provider_fence"
        assert held["missions_run"] == 0
        assert held["suggested_sleep"] > 0
        assert memory.backlog.all()[0].attempt == 1
        assert runner.usage_mission_ids == [f"{item.id}:attempt:1"]

    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=_Sink(), config=config,
    )
    assert supervisor.run()["stopped_by"] == "paused_provider_fence"
    assert memory.backlog.all()[0].attempt == 1
    resumed = memory.backlog.resume_all_paused()
    assert [(row.id, row.attempt) for row in resumed] == [(item.id, 2)]
    assert memory.backlog.resume_all_paused() == []

    retried = supervisor.run()
    assert retried["stopped_by"] == "paused_provider_fence"
    assert retried["missions_run"] == 1
    assert runner.usage_mission_ids == [
        f"{item.id}:attempt:1",
        f"{item.id}:attempt:2",
    ]
    held = supervisor.run()
    assert held["stopped_by"] == "paused_provider_fence"
    assert held["missions_run"] == 0
    stored = memory.backlog.all()[0]
    assert stored.status == "paused_provider_fence"
    assert stored.attempt == 2
    assert len(runner.usage_mission_ids) == 2


def test_provider_fence_does_not_block_independent_ready_work(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    held = BacklogItem.new(
        title="quota held",
        objective="wait for recovery",
        manager_decision={"routed": True, "vertical": "software"},
    )
    held.status = "paused_provider_fence"
    memory.backlog.add(held)
    ready = memory.backlog.add(BacklogItem.new(
        title="independent",
        objective="finish independent work",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    runner = _CompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )

    summary = supervisor.run()

    stored = {item.id: item for item in memory.backlog.all()}
    assert stored[held.id].status == "paused_provider_fence"
    assert stored[held.id].attempt == 1
    assert stored[ready.id].status == "done"
    assert runner.calls == 1
    assert summary["missions_run"] == 1
    assert summary["stopped_by"] == "paused_provider_fence"


def test_supervisor_does_not_auto_resume_operator_pause(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = BacklogItem.new(
        title="operator paused",
        objective="wait",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = "paused_operator"
    memory.backlog.add(item)
    runner = _CompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )

    summary = supervisor.run()

    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert runner.calls == 0
    assert summary["missions_run"] == 0


@pytest.mark.parametrize("new_cap", ["0", "5"])
def test_budget_change_resumes_original_task_without_rebuilding_supervisor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    new_cap: str,
) -> None:
    from argus_skill.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", raising=False)
    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        lambda **_kwargs: 2.0,
    )
    assert write_persisted_knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1")
    memory = LifeMemory.open(tmp_path / "projects" / "paper")
    item = BacklogItem.new(
        title="continue paper",
        objective="revise current evidence",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = "paused_budget"
    memory.backlog.add(item)
    runner = _CompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(
                global_daily_cap_usd=1.0,
                max_missions=1,
                follow_operator_config=True,
            ),
            poll_interval_seconds=0.0,
        ),
    )

    assert supervisor._resume_automatic_pauses() == []
    assert runner.calls == 0
    assert write_persisted_knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", new_cap)

    summary = supervisor.run()

    stored = memory.backlog.all()
    assert len(stored) == 1
    assert stored[0].id == item.id
    assert stored[0].status == "done"
    assert stored[0].attempt == 2
    assert runner.calls == 1
    assert summary["missions_run"] == 1
    assert supervisor.config.budget.global_daily_cap_usd == float(new_cap)


def test_live_budget_keeps_explicit_environment_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1")
    (tmp_path / "config.json").write_text(
        json.dumps({"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "0"}),
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        lambda **_kwargs: 2.0,
    )
    budget = LifeBudget(global_daily_cap_usd=0.0, follow_operator_config=True)

    allowed, reason = budget.can_start(global_root=tmp_path)

    assert allowed is False
    assert "global daily budget exhausted" in reason
