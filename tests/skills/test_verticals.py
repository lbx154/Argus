"""Verticals API + vertical-aware System-(B) stage checklists.

The auto-research loop runs ONE of two *verticals*, selected by a single
``vertical`` field in ``research/PIPELINE_STATE.json``:

* ``research`` (the default) — the full eight-stage paper pipeline. Its
  checklist output is byte-identical to the historical hard-coded behaviour.
* ``speedrun`` — the lean 4-stage (setup/optimize/measure/report)
  numeric-optimization vertical: lower one number (mean val bpb) under a fixed
  wall-clock budget, no paper.

These tests pin the vertical-native API (the old paper|optimize "pipeline mode"
shims are gone):

* ``resolve_vertical`` precedence — env ``ARGUS_SKILL_VERTICAL`` > persisted
  ``vertical`` > ``"research"``.
* ``classify_vertical`` heuristic — a nanochat/optimize objective -> speedrun,
  a paper objective -> research.
* ``format_full_pipeline_checklist`` renders research's 8 stages by default and
  speedrun's 4 stages under ``ARGUS_SKILL_VERTICAL=speedrun``.
* the speedrun reviewer banner is the INNOVATION-COACH override.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_checklists import format_full_pipeline_checklist
from argus_skill.skills.vertical_select import classify_vertical, resolve_vertical
from argus_skill.verticals.speedrun.stages import role_banner as speedrun_role_banner

RESEARCH_STAGES: tuple[str, ...] = (
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
)
SPEEDRUN_STAGES: tuple[str, ...] = ("setup", "optimize", "measure", "report")


def _project(tmp_path: Path, vertical: str | None, *, current: str = "run") -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    payload: dict = {"current_stage": current}
    if vertical is not None:
        payload["vertical"] = vertical
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return tmp_path


# --- resolve_vertical precedence: env > state > research --------------------


def test_resolve_defaults_to_research(tmp_path: Path) -> None:
    # No PIPELINE_STATE at all -> research.
    assert resolve_vertical(tmp_path / "nope") == "research"
    # A state file with no ``vertical`` field -> still research.
    assert resolve_vertical(_project(tmp_path, None)) == "research"


def test_resolve_reads_pipeline_state_vertical(tmp_path: Path) -> None:
    assert resolve_vertical(_project(tmp_path, "speedrun")) == "speedrun"


def test_resolve_env_overrides_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, "research")  # state says research...
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")  # ...env wins.
    assert resolve_vertical(root) == "speedrun"


# --- classify_vertical heuristic -------------------------------------------


def test_classify_nanochat_objective_is_speedrun() -> None:
    assert classify_vertical("lower the mean val_bpb on train.py under budget") == "speedrun"
    assert classify_vertical("minimize the loss and beat the baseline score") == "speedrun"


def test_classify_paper_objective_is_research() -> None:
    assert (
        classify_vertical("write an EMNLP paper with a literature review and draft")
        == "research"
    )
    # Empty / ambiguous -> safe research default.
    assert classify_vertical("") == "research"


# --- format_full_pipeline_checklist is vertical-aware ----------------------


def test_full_pipeline_defaults_to_research_eight_stages(tmp_path: Path) -> None:
    root = _project(tmp_path, "research")
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" in text
    # Research keeps its historical 'final submission gate' header.
    assert "final submission gate" in text


def test_full_pipeline_speedrun_env_yields_four_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")
    root = _project(tmp_path, "research")  # state says research; env forces speedrun.
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in SPEEDRUN_STAGES:
        assert f"### {stage}\n" in text
    # None of the paper-only stages leak in.
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" not in text
    # The header names the vertical instead of the paper submission gate.
    assert "(speedrun)" in text
    assert "final submission gate" not in text


# --- speedrun reviewer banner is the innovation-coach override -------------


def test_speedrun_reviewer_banner_is_innovation_coach() -> None:
    banner = speedrun_role_banner("reviewer")
    assert "INNOVATION COACH" in banner
