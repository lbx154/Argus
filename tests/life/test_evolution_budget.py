from __future__ import annotations

import time
from dataclasses import dataclass

from argus_skill.core.usage import UsageLedger, UsageRecord
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._evolution import (
    _cross_project_propagation_enabled,
    _per_mission_distill_enabled,
)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1


class _Runner:
    def __init__(self) -> None:
        self.usage_contexts = []

    def _set_usage_context(self, mission_id):
        self.usage_contexts.append(mission_id)

    def execute(self, **kwargs):
        return _Outcome()


class _Sink:
    def __init__(self) -> None:
        self.events = []

    def handle_event(self, event):
        self.events.append(event)


def test_source_promotion_is_included_in_global_usage_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "0")
    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_DISTILL", "1")
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(
                global_daily_cap_usd=0.0,
                max_missions=1,
            ),
            project_worktree=tmp_path,
        ),
    )
    item = memory.backlog.add(
        BacklogItem.new(
            title="evolve",
            objective="promote one runtime skill",
        )
    )

    def _tidy(*args, **kwargs):
        now = time.time()
        UsageLedger(memory.root, migrate_legacy=False).append(
            UsageRecord(
                call_id="tidy-call",
                project_id=memory.root.name,
                mission_id=f"{item.id}:attempt:1",
                provider="codex",
                model="gpt-5.5-mini",
                run_label="manager.skill_placement",
                started_at=now,
                completed_at=now,
                status="completed",
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=10,
                reasoning_output_tokens=0,
                premium_requests=0.0,
                pricing_status="priced",
                pricing_tier="test",
                cost_usd=0.25,
                cost_basis="token",
            )
        )
        return {"to_builtin": 1, "to_vertical": 0, "stayed": 0, "errors": 0}

    monkeypatch.setattr("argus_skill.manager.skill_tidy.tidy_after_mission", _tidy)

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    completed = next(
        event for event in sink.events if event.get("type") == "life.mission.completed"
    )
    assert completed["usage_record_count"] == 1
    assert completed["known_cost_usd"] == 0.25
    assert runner.usage_contexts == [f"{item.id}:attempt:1", None]


def test_cross_project_skill_promotion_defaults_on(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", raising=False)

    assert _cross_project_propagation_enabled() is True


def test_cross_project_skill_promotion_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "0")

    assert _cross_project_propagation_enabled() is False


def test_cross_project_skill_promotion_honors_persisted_disable(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", raising=False)
    assert write_persisted_knob("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "off")

    assert _cross_project_propagation_enabled() is False


def test_source_tree_promotion_remains_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PER_MISSION_DISTILL", raising=False)

    assert _per_mission_distill_enabled() is False


def test_supervisor_passes_runner_shared_skill_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_PER_MISSION_DISTILL", raising=False)
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    runner.shared_skills_root = lambda: tmp_path / "custom-shared"
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=1),
            project_worktree=tmp_path,
        ),
    )
    memory.backlog.add(BacklogItem.new(title="evolve", objective="share skill"))
    captured = {}

    def _propagate(*args, **kwargs):
        captured.update(kwargs)
        return {"to_shared": 0, "errors": 0}

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy.propagate_after_mission",
        _propagate,
    )

    assert supervisor.tick() is not None
    assert captured["project_state_dir"] == memory.root
    assert captured["shared_root"] == tmp_path / "custom-shared"
