"""Behavioral regression: bounded mode must disable full_emnlp_gate."""
from __future__ import annotations

import threading
from pathlib import Path

from argus_skill.apps._runtime import _build_repl_supervisor_config
from argus_skill.daemon.life_worker import (
    LifeWorkerConfig,
    _build_supervisor_config,
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


def test_bounded_disables_full_emnlp_gate(tmp_path: Path):
    cfg = _build_supervisor_config(
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
    assert cfg.full_emnlp_gate is False


def test_unbounded_keeps_full_emnlp_gate(tmp_path: Path):
    cfg = _build_supervisor_config(
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
    assert cfg.full_emnlp_gate is True


def test_repl_bounded_disables_full_emnlp_gate(tmp_path: Path):
    cfg = _build_repl_supervisor_config(
        per_mission_cap_usd=10.0,
        daily_cap_usd=180.0,
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
    assert cfg.full_emnlp_gate is False


def test_repl_unbounded_keeps_full_emnlp_gate(tmp_path: Path):
    cfg = _build_repl_supervisor_config(
        per_mission_cap_usd=10.0,
        daily_cap_usd=180.0,
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
    assert cfg.full_emnlp_gate is True
