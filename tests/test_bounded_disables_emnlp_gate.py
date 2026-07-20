"""Behavioral regression: bounded mode must disable full_paper_gate."""
from __future__ import annotations

import threading
from pathlib import Path

from argus_skill.apps._runtime import (
    _build_supervisor_config as _build_runtime_supervisor_config,
)
from argus_skill.daemon.life_worker import (
    LifeWorkerConfig,
)
from argus_skill.daemon.life_worker import (
    _build_supervisor_config as _build_worker_supervisor_config,
)


def _worker_cfg(tmp_path: Path, *, open_ended: bool) -> LifeWorkerConfig:
    return LifeWorkerConfig(
        life_dir=tmp_path / "life",
        global_root=None,
        project_workdir=tmp_path,
        backend="memory",
        continuous=True,
        continuous_objective="bounded survey",
        continuous_open_ended=open_ended,
    )


def test_worker_bounded_disables_full_paper_gate(tmp_path: Path):
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=False),
        runtime_root=tmp_path / "life",
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="bounded survey",
        continuous_provider=lambda: (True, "bounded survey"),
        planner_runtime_context_provider=lambda: "",
        planner_restart_handler=lambda _reason: False,
        post_mission_hook=lambda: "",
    )

    assert cfg.open_ended is False
    assert cfg.full_paper_gate is False


def test_worker_unresolved_unbounded_project_does_not_assume_emnlp(tmp_path: Path):
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=True),
        runtime_root=tmp_path / "life",
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="open ended paper",
        continuous_provider=lambda: (True, "open ended paper"),
        planner_runtime_context_provider=lambda: "",
        planner_restart_handler=lambda _reason: False,
        post_mission_hook=lambda: "",
    )

    assert cfg.open_ended is True
    assert cfg.paper_mission is False
    assert cfg.full_paper_gate is False


def test_bounded_disables_full_paper_gate(tmp_path: Path):
    cfg = _build_runtime_supervisor_config(
        global_daily_cap_usd=0.0,
        once=False,
        max_missions=1,
        project_worktree=tmp_path,
        stop_event=threading.Event(),
        project_root=tmp_path / "life",
        runtime_context="",
        continuous=True,
        continuous_objective="bounded survey",
        open_ended=False,
    )

    assert cfg.open_ended is False
    assert cfg.full_paper_gate is False


def test_unresolved_unbounded_project_does_not_assume_emnlp(tmp_path: Path):
    cfg = _build_runtime_supervisor_config(
        global_daily_cap_usd=0.0,
        once=False,
        max_missions=1,
        project_worktree=tmp_path,
        stop_event=threading.Event(),
        project_root=tmp_path / "life",
        runtime_context="",
        continuous=True,
        continuous_objective="open ended paper",
        open_ended=True,
    )

    assert cfg.open_ended is True
    assert cfg.paper_mission is False
    assert cfg.full_paper_gate is False


def _config_for_vertical(tmp_path: Path, vertical: str, *, open_ended: bool = True):
    from argus_skill.skills.vertical_select import persist_vertical

    root = tmp_path / "life"
    persist_vertical(root, vertical)  # the Manager's decision, persisted
    return _build_runtime_supervisor_config(
        global_daily_cap_usd=0.0,
        once=False,
        max_missions=1,
        project_worktree=tmp_path,
        stop_event=threading.Event(),
        project_root=root,
        runtime_context="",
        continuous=True,
        continuous_objective="do the thing",
        open_ended=open_ended,
    )


def test_supervisor_paper_mission_off_for_optimize_vertical(tmp_path: Path):
    # Regression: an optimize vertical (kernelbench) must NOT carry paper_mission
    # into the supervisor config, or every bounded backlog item gets the
    # "continue through adjacent paper blockers" guidance (see
    # _render_backlog_item_metadata). The gate follows the resolved vertical.
    cfg = _config_for_vertical(tmp_path, "kernelbench")
    assert cfg.paper_mission is False


def test_supervisor_paper_mission_on_for_research_vertical(tmp_path: Path):
    cfg = _config_for_vertical(tmp_path, "research")
    assert cfg.paper_mission is True
    assert cfg.full_paper_gate is True


def test_worker_supervisor_enables_paper_mode_only_after_research_resolution(
    tmp_path: Path,
):
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=True),
        runtime_root=tmp_path / "life",
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="paper campaign",
        continuous_provider=lambda: (True, "paper campaign"),
        planner_runtime_context_provider=lambda: "",
        planner_restart_handler=lambda _reason: False,
        post_mission_hook=lambda: "",
    )

    assert cfg.paper_mission is True
    assert cfg.full_paper_gate is True
    assert cfg.artifact_root == tmp_path
