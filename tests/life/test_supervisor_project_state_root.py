"""A split-memory daemon looks the project's vertical up under the project.

The daemon of a fingerprinted session carries a ``MemoryBundle`` whose ``root``
is the global home shared by every project, while Manager-owned state (the
pipeline decision, delivery manifests, venue selection) lives under its
``project_root``. Handing the global home to a vertical lookup answered
"undecided" for a project that had been classified long ago: the research
fallback was logged four times per settlement on a live run, and the terminal
idle signature hashed the wrong pipeline state.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from argus.life.event_log import JsonlEventSink
from argus.life.memory import GlobalMemory, LifeMemory, MemoryBundle, ProjectMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig, _idle_cycle
from argus.skills import vertical_select
from argus.skills.vertical_select import persist_vertical, resolve_vertical


class _Runner:
    pass


def _config(workspace: Path, **overrides: object) -> LifeSupervisorConfig:
    settings: dict[str, object] = {
        "budget": LifeBudget(),
        "poll_interval_seconds": 0.01,
        "continuous": True,
        "continuous_objective": "smoke",
        "open_ended": False,
        "final_certification_gate": False,
        "project_worktree": workspace,
    }
    settings.update(overrides)
    return LifeSupervisorConfig(**settings)  # type: ignore[arg-type]


def _split_memory_supervisor(tmp_path: Path) -> tuple[LifeSupervisor, Path, Path, Path]:
    global_root = tmp_path / "home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    project = ProjectMemory.open("s-splitproj", label="s-splitproj", global_root=global_root)
    memory = MemoryBundle(
        global_mem=GlobalMemory.open(global_root),
        project=project,
        project_worktree=workspace,
    )
    memory.init()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=project.root, verbosity="full"),
        config=_config(workspace, project_state_dir=project.root),
        planner_runner=object(),
    )
    persist_vertical(project.root, "software", workflow_mode="direct")
    return supervisor, global_root, project.root, workspace


def test_project_state_root_is_the_project_not_the_global_home(tmp_path: Path) -> None:
    supervisor, global_root, project_root, _workspace = _split_memory_supervisor(tmp_path)

    assert supervisor.memory.root == global_root
    assert supervisor._project_state_root() == project_root


def test_project_state_root_of_a_plain_life_memory_is_its_root(tmp_path: Path) -> None:
    life = tmp_path / "life"
    workspace = tmp_path / "work"
    workspace.mkdir()
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=_config(workspace),
        planner_runner=object(),
    )

    assert supervisor._project_state_root() == life


def test_idle_signature_hashes_the_project_state_not_the_global_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    supervisor, global_root, project_root, workspace = _split_memory_supervisor(tmp_path)
    supervisor._current_pipeline_stage = lambda: "delivery"  # type: ignore[method-assign]
    seen: dict[str, object] = {}
    real = _idle_cycle.build_terminal_idle_signature

    def recording(**kwargs: object) -> str:
        seen.update(kwargs)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_idle_cycle, "build_terminal_idle_signature", recording)
    vertical_select._FALLBACK_WARNED_ROOTS.clear()

    with caplog.at_level(logging.WARNING, logger="argus.skills.vertical_select"):
        signature = supervisor._open_ended_terminal_idle_signature()

    assert signature
    assert seen["state_root"] == project_root
    assert seen["artifact_root"] == project_root
    assert seen["project_root"] == workspace
    assert not [
        record for record in caplog.records
        if "no Manager vertical resolved" in record.getMessage()
    ], "the classified project must not fall back to research"
    assert str(global_root) not in caplog.text


def test_research_fallback_is_reported_once_per_root(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    undecided = tmp_path / "undecided"
    undecided.mkdir()
    vertical_select._FALLBACK_WARNED_ROOTS.clear()

    with caplog.at_level(logging.DEBUG, logger="argus.skills.vertical_select"):
        first = resolve_vertical(undecided)
        second = resolve_vertical(undecided)

    assert first == second == "research"
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and "no Manager vertical resolved" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert str(undecided) in warnings[0].getMessage()
