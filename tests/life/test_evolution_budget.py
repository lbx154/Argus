from __future__ import annotations

from dataclasses import dataclass

from argus.core.knobs import learning_enabled
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


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


def test_learning_switch_defaults_on_and_honors_a_persisted_off(tmp_path, monkeypatch) -> None:
    from argus.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_LEARNING", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "0")  # retired, ignored
    assert learning_enabled() is True
    assert write_persisted_knob("ARGUS_SKILL_LEARNING", "off")
    assert learning_enabled() is False
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "1")
    assert learning_enabled() is True


def test_reviewer_confirmed_research_result_waits_for_the_reflection(tmp_path) -> None:
    """The judgment rides only in the reflection call; settlement makes no model call."""
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            project_worktree=tmp_path / "campaign",
            role_skill_maintenance_enabled=False,
        ),
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

    pending = supervisor._pending_reviewed_fact
    assert pending["source_campaign"] == "campaign-03"
    assert pending["research_result"] == result
    assert pending["evidence_refs"] == ["result.json"]
    assert "verified_new_result" in pending["summary"]
