from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


@dataclass
class _Division:
    task: str
    vertical: str = "research"
    kind: str = "research"
    regular: bool = True
    stages: list[str] | None = None


class _Manager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def divide(self, task: str, *, ask_on_new_domain: bool = False) -> _Division:
        self.calls.append(task)
        return _Division(task=task, stages=["research", "draft"])


class _Runner:
    def __init__(self) -> None:
        self.manager = _Manager()
        self.executed: list[str] = []

    def execute(self, **kwargs):
        self.executed.append(str(kwargs["objective"]))

        class _Outcome:
            success = True
            status = "done"
            stop_reason = ""
            rounds = 1
            matched_skill_name = ""
            skill_distilled = False
            final_submission_certified = False
            completion_evidence = ""
            planner_report = {}
            checklist_feedback = {}
            step_back = None
            process_lesson = ""
            stage_transition = {}
            auth_failure = False

        return _Outcome()


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


def test_each_backlog_item_goes_through_manager_before_engineer(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    item = BacklogItem.new(
        title="write a report",
        objective="write a report about agent systems",
    )
    memory.backlog.add(item)
    runner = _Runner()
    sink = _Sink()
    sup = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            project_worktree=tmp_path,
        ),
    )

    result = sup.tick()

    assert result is not None
    assert runner.manager.calls == [item.objective]
    assert runner.executed == [item.objective]
    event_types = [event["type"] for event in sink.events]
    assert event_types.index("life.manager.started") < event_types.index(
        "life.mission.started"
    )
    assert event_types.index("life.manager.completed") < event_types.index(
        "life.mission.started"
    )
