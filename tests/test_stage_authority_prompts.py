"""Guard tests: pipeline stage-transition authority belongs to the Manager only.

These lock the prompt surgery (Step 4 of the stage-authority change) that removed
every instruction telling the engineer / reviewer / planner to write pipeline
stage state or call ``rollback_stage`` directly. The Manager is the sole
post-bootstrap writer of ``current_stage``; the others only advise.
"""
from __future__ import annotations

from pathlib import Path

import argus_skill

ROOT = Path(argus_skill.__file__).resolve().parent

# The specific agent-facing shell recipe the prompts used to emit. Its absence is
# the regression guard (a passing comment mentioning rollback_stage won't match
# this full call shape).
_ROLLBACK_RECIPE = "rollback_stage('.', target_stage="


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_planner_preamble_gives_stage_authority_to_manager() -> None:
    from argus_skill.planner.planner import _PLANNER_SYSTEM_PREAMBLE as preamble

    assert "Manager can advance `current_stage`" in preamble
    assert "Manager owns stage transitions" in preamble
    # the old "the reviewer advances the stage" wording is gone
    assert "until the reviewer has" not in preamble


def test_planner_source_has_no_rollback_recipe() -> None:
    assert _ROLLBACK_RECIPE not in _src("planner/planner.py")


def test_reviewer_reports_upstream_defects_instead_of_rolling_back() -> None:
    src = _src("reviewer/_core.py")
    assert _ROLLBACK_RECIPE not in src
    assert "the Manager owns rollback" in src


def test_engineer_prompt_forbids_editing_pipeline_stage() -> None:
    assert "Pipeline stage is Manager-owned" in _src("loop.py")


def test_auto_research_skill_does_not_tell_engineer_to_advance_stage() -> None:
    md = _src("builtin_skills/engineer/auto-research-pipeline.md")
    assert "advance to the next stage and update" not in md
    assert "Manager-owned" in md
