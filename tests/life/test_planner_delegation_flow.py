"""Planner must delegate implementation and keep standing campaigns moving."""

from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.daemon.state import write_continuous_config
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.planner import PlannerConfig
from argus_skill.skills.vertical_select import persist_vertical


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


def _supervisor(project: Path, life: Path, planner: _PlannerBackend) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="keep optimizing Argus",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    # Isolate this flow test from host continuous.json state. The Planner class
    # still receives and forwards this provider in production.
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        open_ended=True,
    )
    return supervisor


def test_planner_delegates_to_engineer_and_continues_after_one_increment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=delegate the first bounded optimization",
            "TASK_KEY=first",
            "TASK_TITLE=Remove redundant snapshot prewarm",
            "TASK_OBJECTIVE=Change the prewarm scheduling and add a regression test.",
            "TASK_ACCEPTANCE_CHECK=pytest tests/webapi/test_index_cache.py",
        ]),
        "PROJECT_DONE=true\nREASON=finished one optimization",
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=continue the standing campaign with a distinct issue",
            "TASK_KEY=second",
            "TASK_TITLE=Deduplicate Manager reply rows",
            "TASK_OBJECTIVE=Use one message identity for live and persisted replies.",
            "TASK_ACCEPTANCE_CHECK=npm test -- stream.test.ts",
        ]),
    ])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True
    first = supervisor.memory.backlog.pending()
    assert [item.title for item in first] == ["Remove redundant snapshot prewarm"]
    supervisor.memory.backlog.update(first[0].id, status="done")

    assert supervisor._plan_next_work() is True
    pending = supervisor.memory.backlog.pending()
    assert [item.title for item in pending] == ["Deduplicate Manager reply rows"]

    assert len(planner.calls) == 3
    assert all(call["options"].sandbox_mode is None for call in planner.calls)
    assert all(call["options"].dangerous_yolo is True for call in planner.calls)
    assert not list(project.glob("**/*.py")), "Planner must not create implementation files"


def test_new_continuous_generation_interrupts_obsolete_planner(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    memory = LifeMemory.open(life)
    write_continuous_config(life, enabled=True, objective="old objective")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="old objective",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=object(),
    )

    config = supervisor._planner_config()
    provider = config.external_interrupt_reason_provider
    assert provider() is None
    assert config.add_dirs == [str(life)]

    write_continuous_config(life, enabled=True, objective="new operator objective")

    assert provider() == "planner superseded by newer continuous generation"
