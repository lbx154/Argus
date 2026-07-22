"""End-to-end AAAI venue path: scaffold seed -> resolve -> every format gate.

Proves the seam composes: a project seeded with venue=aaai resolves to the AAAI
profile, and the layout gate, structural-minimums gate, stage checklist floor,
and stage_check reviewer checklist all switch to AAAI rules — while the same
inputs keep EMNLP behavior for an EMNLP project.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_machine import format_stage_checklist
from argus_skill.skills.venue_profiles import get_venue_profile, resolve_venue_profile
from argus_skill.tools.stage_check import _reviewer_checklist_for
from argus_skill.verticals.research.paper_layout_review import _deterministic_assessment
from argus_skill.verticals.research.paper_structural_minimums import (
    validate_paper_structural_minimums,
)

pytestmark = pytest.mark.e2e


def _seed_project(tmp_path: Path, venue: str) -> Path:
    """Seed the minimal venue-bearing pipeline state a real project would have.

    The standalone paper scaffolder was retired, so this writes the only field
    the venue gates actually read — ``research/PIPELINE_STATE.json``'s
    ``target_venue`` — directly, mirroring what the daemon bootstrap persists.
    """
    profile = get_venue_profile(venue)
    state = {
        "current_stage": "research",
        "vertical": "research",
        "objective": "obj",
        "target_venue": profile.key,
    }
    target = tmp_path / "research" / "PIPELINE_STATE.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return tmp_path


def test_aaai_scaffold_resolves_to_aaai_profile(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, "aaai")
    state = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert state["target_venue"] == "AAAI"
    assert resolve_venue_profile(root).key == "AAAI"


def test_aaai_project_layout_gate_uses_aaai_budget(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, "aaai")
    venue = resolve_venue_profile(root)
    # Conclusion p7 / References p8: correct for AAAI, underfilled for EMNLP.
    layout = "\f".join(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion x", "References\n[1] a"]
    )
    codes = {
        i["code"]
        for i in _deterministic_assessment(
            tex_text="", log_text="", layout_text=layout, threshold=3.5, venue=venue
        )["issues"]
    }
    assert "references_before_full_body" not in codes


def test_aaai_project_structural_gate_flags_missing_pdfinfo(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, "aaai")
    (root / "paper").mkdir(parents=True, exist_ok=True)
    (root / "paper" / "main.tex").write_text(
        "\\documentclass{article}\n\\section{Intro}\n", encoding="utf-8"
    )
    codes = {i.code for i in validate_paper_structural_minimums(root).issues}
    assert "missing_pdfinfo_block" in codes
    assert "missing_aaai_style_package" in codes


def test_aaai_project_checklist_and_reviewer_are_aaai(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, "aaai")
    sub = format_stage_checklist("submission", role="reviewer", project_root=root)
    assert "Anonymous submission" in sub
    assert "Anonymous EMNLP Submission" not in sub
    # Reviewer checklist points at the AAAI reviewer skill.
    skill, _instr, _files = _reviewer_checklist_for("review", resolve_venue_profile(root))
    assert skill == "reviewer/aaai-academic-language-review.md"


def test_emnlp_project_is_unchanged(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, "emnlp")
    assert resolve_venue_profile(root).key == "EMNLP"
    sub = format_stage_checklist("submission", role="reviewer", project_root=root)
    assert "Anonymous EMNLP Submission" in sub
    skill, _i, _f = _reviewer_checklist_for("review", resolve_venue_profile(root))
    assert skill == "reviewer/emnlp-academic-language-review.md"
