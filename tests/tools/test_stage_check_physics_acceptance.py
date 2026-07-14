"""Acceptance tests: stage_check loads the physics vertical natively.

Self-contained — each test builds a throwaway project root under ``tmp_path``
and never reads Phase 3 pipeline outputs or any literature-distillation artifact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from argus_skill.tools import stage_check

# The scope/model/execute/review evidence stages pass on PIPELINE_STATE alone.
# The terminal ``manuscript`` stage additionally requires the full research-paper
# package, so it fails-closed on a bare project — covered by its own test below.
PHYSICS_STAGES = ("scope", "model", "execute", "review")


def _seed_physics_project(root: Path, *, current_stage: str = "scope") -> None:
    """Write the minimal PIPELINE_STATE.json a physics stage-check needs."""
    state_path = root / "research" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"vertical": "physics", "current_stage": current_stage}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("stage", PHYSICS_STAGES)
def test_stage_check_loads_physics_vertical_for_each_stage(
    tmp_path: Path,
    monkeypatch,
    capsys,
    stage: str,
) -> None:
    # Requirement 10: --vertical physics resolves to the physics vertical for
    # every stage, passes its shell checks, and never falls back.
    _seed_physics_project(tmp_path, current_stage=stage)
    before = (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--vertical",
            "physics",
            "--stage",
            stage,
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert f"📋 Stage: {stage}  (vertical: physics)" in out
    assert "✅ Pipeline state present" in out
    # No silent fallback to research or math.
    assert "(vertical: research)" not in out
    assert "(vertical: math)" not in out
    # stage_check must not mutate Manager-owned pipeline state.
    assert (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8") == before


def test_stage_check_physics_reads_vertical_from_pipeline_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # Without an explicit --vertical flag, the physics vertical is auto-detected
    # from research/PIPELINE_STATE.json (no fallback to research or math).
    _seed_physics_project(tmp_path, current_stage="model")

    monkeypatch.setattr(
        sys,
        "argv",
        ["stage-check", "--project-root", str(tmp_path), "--stage", "model"],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 Stage: model  (vertical: physics)" in out
    assert "(vertical: research)" not in out
    assert "(vertical: math)" not in out


def test_stage_check_auto_detects_persisted_physics_vertical(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # End-to-end: the real persist path (Manager's persist_vertical) writes
    # vertical=physics and seeds stage "scope"; stage_check then auto-detects it
    # with no --vertical / --stage flag and runs the physics scope stage without
    # falling back to research or math.
    from argus_skill.skills.vertical_select import persist_vertical

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    persist_vertical(tmp_path, "physics")

    monkeypatch.setattr(
        sys,
        "argv",
        ["stage-check", "--project-root", str(tmp_path)],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 Stage: scope  (vertical: physics)" in out
    assert "(vertical: research)" not in out
    assert "(vertical: math)" not in out


def test_stage_check_physics_missing_pipeline_state_fails_normally(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # The physics shell check is real: with no PIPELINE_STATE.json the check
    # fails (status 1) while still resolving the physics vertical, proving the
    # gate is not a rubber stamp.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--vertical",
            "physics",
            "--stage",
            "scope",
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "(vertical: physics)" in out
    assert "❌ Pipeline state present" in out


def test_stage_check_manuscript_stage_fails_closed_without_paper_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # The terminal manuscript stage resolves to physics, but its shell check
    # fails-closed until the full research-paper package exists — there is no
    # optional mode and no marker-file skip.
    _seed_physics_project(tmp_path, current_stage="manuscript")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--vertical",
            "physics",
            "--stage",
            "manuscript",
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "📋 Stage: manuscript  (vertical: physics)" in out
    assert "(vertical: research)" not in out
    assert "(vertical: math)" not in out
