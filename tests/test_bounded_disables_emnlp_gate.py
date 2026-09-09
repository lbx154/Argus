"""Behavioral regression: bounded mode must disable final_certification_gate."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

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


def test_worker_bounded_disables_final_certification_gate(tmp_path: Path):
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=False),
        runtime_root=tmp_path / "life",
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="bounded survey",
        continuous_provider=lambda: (True, "bounded survey"),
        post_mission_hook=lambda: "",
    )

    assert cfg.open_ended is False
    assert cfg.final_certification_gate is False
    assert cfg.budget.follow_operator_config is True


def test_worker_unresolved_unbounded_project_does_not_assume_emnlp(tmp_path: Path):
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=True),
        runtime_root=tmp_path / "life",
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="open ended paper",
        continuous_provider=lambda: (True, "open ended paper"),
        post_mission_hook=lambda: "",
    )

    assert cfg.open_ended is True
    assert cfg.paper_mission is False
    assert cfg.final_certification_gate is False


def test_bounded_disables_final_certification_gate(tmp_path: Path):
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
    assert cfg.final_certification_gate is False


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
    assert cfg.final_certification_gate is False


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
    assert cfg.final_certification_gate is True


def test_worker_supervisor_enables_paper_mode_only_after_research_resolution(
    tmp_path: Path,
    monkeypatch,
):
    from argus_skill.skills.vertical_select import persist_vertical

    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "no-prompts"))
    runtime_root = tmp_path / "life"
    persist_vertical(runtime_root, "research")
    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=True),
        runtime_root=runtime_root,
        stop_event=threading.Event(),
        init_continuous=True,
        init_objective="paper campaign",
        continuous_provider=lambda: (True, "paper campaign"),
        post_mission_hook=lambda: "",
    )

    assert cfg.paper_mission is True
    assert cfg.final_certification_gate is True
    assert cfg.artifact_root == runtime_root
    assert cfg.project_worktree == tmp_path
    assert "## Research team" in cfg.runtime_context
    assert "verify changed claims" in cfg.runtime_context
    assert "Do not verify your own output" not in cfg.runtime_context
    assert "## Python environments" not in cfg.runtime_context


@pytest.mark.parametrize(
    ("stage", "has_paper_guidance"),
    [("idea", False), ("experiment", False), ("paper", True), ("review", True)],
)
def test_direct_research_revision_guidance_preserves_bounded_stage(
    tmp_path: Path,
    monkeypatch,
    stage: str,
    has_paper_guidance: bool,
) -> None:
    from argus_skill.core.pipeline_state import read_pipeline_state
    from argus_skill.skills.vertical_select import persist_vertical

    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "no-prompts"))
    root = tmp_path / "life"
    persist_vertical(root, "research", workflow_mode="direct", start_stage=stage)
    before = read_pipeline_state(root)

    cfg = _build_worker_supervisor_config(
        _worker_cfg(tmp_path, open_ended=False),
        runtime_root=root,
        stop_event=threading.Event(),
        init_continuous=False,
        init_objective="work on the selected stage",
        continuous_provider=None,
        post_mission_hook=None,
    )

    assert ("## Research team" in cfg.runtime_context) is has_paper_guidance
    assert "## Python environments" not in cfg.runtime_context
    assert cfg.paper_mission is False
    assert cfg.final_certification_gate is False
    assert cfg.continuous is False
    assert read_pipeline_state(root) == before
