from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def handle_event(self, event):  # noqa: ANN001
        return None


class _CaptureRunner:
    def __init__(self) -> None:
        self.kwargs = None

    def execute(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        return SimpleNamespace(
            success=True,
            status="done",
            stop_reason="",
            rounds=1,
            stage_transition={"action": "hold"},
        )


def test_bounded_dag_node_keeps_vertical_stage_workflow(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(
        BacklogItem.new(
            title="scope",
            objective="complete scope",
            tags=["planner", "bounded_dag_node", "scope:bounded"],
        )
    )
    runner = _CaptureRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    supervisor.tick()

    assert runner.kwargs is not None
    assert "workflow_mode_override" not in runner.kwargs
    assert runner.kwargs["preplanned"] is True
    assert runner.kwargs["require_independent_review"] is False
    assert runner.kwargs["max_rounds_override"] >= 2


def test_stage_closing_item_requires_independent_review(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(
        BacklogItem.new(
            title="close research",
            objective="complete and certify the research gate",
            tags=[
                "planner",
                "scope:bounded",
                "stage_closing",
                "review:required",
            ],
        )
    )
    runner = _CaptureRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    supervisor.tick()

    assert runner.kwargs is not None
    assert runner.kwargs["require_independent_review"] is True
