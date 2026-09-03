"""Guard tests: pipeline stage-transition authority belongs to the Manager only.

These lock the prompt surgery (Step 4 of the stage-authority change) that removed
every instruction telling the engineer / reviewer / planner to write pipeline
stage state or call ``rollback_stage`` directly. The Manager is the sole
writer of ``current_stage`` after initial state creation; the others only advise.
"""

from __future__ import annotations

from pathlib import Path

import argus_skill
from argus_skill.skills.role_context import load_builtin_skill_text

ROOT = Path(argus_skill.__file__).resolve().parent

# The specific agent-facing shell recipe the prompts used to emit. Its absence is
# the regression guard (a passing comment mentioning rollback_stage won't match
# this full call shape).
_ROLLBACK_RECIPE = "rollback_stage('.', target_stage="


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_planner_role_gives_stage_authority_to_manager() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "Manager alone changes" in text
    assert ".argus/PIPELINE_STATE.json" in text
    assert "report an upstream stage defect" in text
    assert "does not by itself prove completion" in text
    # the old "the reviewer advances the stage" wording is gone
    assert "until the reviewer has" not in text


def test_planner_source_has_no_rollback_recipe() -> None:
    assert _ROLLBACK_RECIPE not in _src("planner/planner.py")


def test_reviewer_reports_upstream_defects_instead_of_rolling_back() -> None:
    src = _src("roles/prompts/reviewer.py")
    assert _ROLLBACK_RECIPE not in src
    assert "Manager owns rollback" in src


def test_build_playbook_does_not_tell_engineer_to_advance_stage() -> None:
    md = _src("verticals/research/skills/research-build-playbook.md")
    assert "advance to the next stage and update" not in md
    assert "Manager alone advances" in md
