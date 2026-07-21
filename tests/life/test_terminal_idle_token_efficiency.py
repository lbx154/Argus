"""Token-efficiency regressions for terminal open-ended campaigns."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_RETRY, PLAN_TERMINAL_IDLE
from argus_skill.planner import PlannerVerdict
from argus_skill.skills.vertical_select import persist_vertical


class _Runner:
    pass


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    config = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=config,
        planner_runner=object(),
    )
    persist_vertical(project, "software")
    supervisor._vertical_resolved = True
    supervisor._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    return supervisor


def test_restart_ignores_agent_bookkeeping_without_another_planner_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(project_done=True, reason="verified terminal")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    first = _supervisor(project, life)
    assert first._plan_next_work() == PLAN_RETRY
    assert calls == 1

    # These files are written by Argus itself after or around a verdict. They
    # preserve process evidence but do not constitute new operator intent or a
    # changed accepted project state.
    wiki = project / ".autors" / "project" / "wiki" / "sources" / "runs"
    wiki.mkdir(parents=True)
    (wiki / "round.md").write_text("reviewed run\n", encoding="utf-8")
    raw = project / "research" / "raw"
    raw.mkdir(parents=True)
    (raw / "profile.log").write_text("counter output\n", encoding="utf-8")
    (project / "research" / "GROUND_TRUTH.md").write_text(
        "planner refresh\n",
        encoding="utf-8",
    )
    ignored_review = project / ".venv" / "deep" / "package" / "REVIEW.md"
    ignored_review.parent.mkdir(parents=True)
    ignored_review.write_text("dependency metadata\n", encoding="utf-8")

    restarted = _supervisor(project, life)
    assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
    assert calls == 1


def test_tracked_source_change_invalidates_terminal_signature(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "kernel.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "kernel.py"], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Argus Test",
            "-c",
            "user.email=argus@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=project,
        check=True,
    )
    supervisor = _supervisor(project, tmp_path / "life")
    first = supervisor._open_ended_terminal_idle_signature()

    source.write_text("VALUE = 2\n", encoding="utf-8")

    assert supervisor._open_ended_terminal_idle_signature() != first


def test_nonsemantic_json_timestamps_remain_ignored(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    state_path = project / "research" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    first = supervisor._open_ended_terminal_idle_signature()

    state["updated_at"] = 2
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    assert supervisor._open_ended_terminal_idle_signature() == first
