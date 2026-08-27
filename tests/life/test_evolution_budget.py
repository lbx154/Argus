from __future__ import annotations

from dataclasses import dataclass

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._evolution import _cross_project_propagation_enabled


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


def test_supervisor_passes_runner_shared_skill_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", raising=False)
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
    assert captured["mission_objective"] == "share skill"
    assert captured["mission_success"] is True


def test_failed_mission_still_reaches_team_learning_review(
    tmp_path,
    monkeypatch,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=1),
            project_worktree=tmp_path,
        ),
    )
    captured = {}

    def _propagate(*args, **kwargs):
        captured.update(kwargs)
        return {"to_shared": 0, "errors": 0}

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy.propagate_after_mission",
        _propagate,
    )
    monkeypatch.setattr(
        "argus_skill.manager.domain_tidy.tidy_domains_after_mission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failed missions must not promote data domains")
        ),
    )

    supervisor._evolve_runtime_skills_after_mission(
        success=False,
        usage_mission_id="failed-attempt",
        mission_objective="Retry the fixed swap threshold",
        mission_result="status=error; reason=the same fixed threshold blocked again",
    )

    assert captured["mission_success"] is False
    assert "same fixed threshold" in captured["mission_result"]


def test_reviewer_confirmed_research_result_reaches_fact_judgment(
    tmp_path,
    monkeypatch,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=1),
            project_worktree=tmp_path / "campaign",
            role_skill_maintenance_enabled=False,
        ),
    )
    captured = {}

    def _review(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "argus_skill.manager.reviewed_facts.review_and_append_fact",
        _review,
    )
    result = {"result_class": "verified_new_result", "evidence": ["result.json"]}

    supervisor._evolve_runtime_skills_after_mission(
        success=True,
        usage_mission_id="attempt",
        reviewer_source="reviewer",
        reviewer_reason="The held-out effect is confirmed.",
        research_result=result,
        evidence_refs=("result.json",),
        source_campaign="campaign-03",
    )

    assert captured["source_campaign"] == "campaign-03"
    assert captured["research_result"] == result
    assert captured["evidence_refs"] == ("result.json",)
