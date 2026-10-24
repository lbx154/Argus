"""Token-efficiency regressions for terminal open-ended campaigns."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.skills.stage_machine import completion_contract_fingerprint
from argus.skills.vertical_select import persist_vertical
from argus.verticals._base import load_vertical_contract


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
        final_certification_gate=False,
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
    # This regression is about open-ended terminal-idle reuse, not the staged
    # Goal Gate. Keep the fixture on the direct topology so a planner
    # ``project_done`` reaches the terminal-idle path under test.
    persist_vertical(project, "software", workflow_mode="direct")
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "delivery"
    state["stages"] = {"delivery": {"status": "done"}}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    version = load_vertical_contract("software", project_root=project).completion_contract_version
    state["stages"]["delivery"].update(
        {
            "completion_contract_version": version,
            "completion_contract_sha256": completion_contract_fingerprint(
                project,
                "delivery",
                version=version,
            ),
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    supervisor._vertical_resolved = True
    supervisor._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    return supervisor


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
