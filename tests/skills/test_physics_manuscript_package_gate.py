"""Regression: the review-stage manuscript-package advisory gate surfaces the
deterministic terminal-contract failures + an executable repair loop as soon as a
paper package exists — the root-cause fix for the Case B ``no_progress`` stall.

Before the fix, ``verify_all_deliverables`` was only run at the terminal
``manuscript`` stage HARD gate, so a paper package produced at ``review`` never
handed the agent a concrete failure list; the reviewer stalled and the mission
died ``no_progress`` before the manuscript stage. This gate runs the SAME checker
at ``review`` (advisory), writes the failure list through the shared
``research_gates`` machinery, and thereby feeds it into the physics ``role_banner``
repair blocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.skills.research_gates import (
    gate_file_prefix,
    render_active_repair_blocks,
)
from argus_skill.verticals.physics.gates import manuscript_package as mpkg

PREFIX = gate_file_prefix(mpkg.GATE_ID)  # "MANUSCRIPT_PACKAGE_GATE"


def _write_partial_paper(root: Path) -> None:
    """A started-but-incomplete paper package: MANUSCRIPT.md present with the
    required section headings, but FIGURE_LEGENDS.md / REFERENCES.bib / CLAIMS.csv /
    the LaTeX layer / figures are all missing -> real deterministic failures."""
    (root / "MANUSCRIPT.md").write_text(
        "# Title\n\n## Abstract\n\n## Introduction\n\n## Background\n\n"
        "## Model/Theory\n\n## Methods\n\n## Results\n\n## Discussion\n\n"
        "## Limitations\n\n## Conclusion\n\n## References\n\n## Data & Code Availability\n",
        encoding="utf-8",
    )


def test_no_paper_package_passes(tmp_path: Path) -> None:
    """No MANUSCRIPT.md/.tex yet -> nothing to check at review (paper is a
    manuscript-stage deliverable) -> gate passes, writes no repair state."""
    passed, failures = mpkg.run_gate(tmp_path)
    assert passed is True and failures == []
    assert not (tmp_path / "research" / f"{PREFIX}_STATE.json").exists()
    assert render_active_repair_blocks(tmp_path) == ""


def test_present_but_failing_surfaces_failures_and_repair_loop(tmp_path: Path) -> None:
    """A paper package that does NOT satisfy the contract -> the exact deterministic
    failure list is produced, persisted as repair state, and rendered into the
    role_banner repair blocks (so the agent gets an executable repair loop)."""
    _write_partial_paper(tmp_path)

    passed, failures = mpkg.run_gate(tmp_path)
    assert passed is False
    assert failures, "expected deterministic manuscript-contract failures"
    # the concrete, checkable items the agent must fix are present verbatim
    blob = " | ".join(f["message"] for f in failures)
    assert "FIGURE_LEGENDS.md" in blob
    assert "REFERENCES.bib" in blob or "references.md" in blob
    # every failure is advisory at review (never a hard blocker)
    assert all(f["blocks_progress"] is False for f in failures)
    # persisted repair state exists
    assert (tmp_path / "research" / f"{PREFIX}_STATE.json").exists()
    assert (tmp_path / "research" / f"{PREFIX}_REPAIR_TASKS.md").exists()
    # and it flows into the physics role_banner repair blocks -> reaches the agent
    blocks = render_active_repair_blocks(tmp_path)
    assert PREFIX in blocks
    assert "FIGURE_LEGENDS.md" in blocks


def test_review_stage_role_banner_does_not_inject_gate_repairs(tmp_path: Path) -> None:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        '{"current_stage": "review", "vertical": "physics"}', encoding="utf-8"
    )
    _write_partial_paper(tmp_path)
    mpkg.run_gate(tmp_path)

    from argus_skill.verticals.physics.stages import role_banner

    banner = role_banner("engineer", project_root=tmp_path)
    assert PREFIX not in banner
    assert "FIGURE_LEGENDS.md" not in banner
    assert "## Review focus" in banner


def test_advisory_cli_exits_zero_even_when_failing(tmp_path: Path) -> None:
    """The gate is advisory at review: main() returns 0 even with failures, so it
    never hard-blocks review->manuscript."""
    _write_partial_paper(tmp_path)
    rc = mpkg.main(["check", "--project-root", str(tmp_path), "--advisory"])
    assert rc == 0


def test_passing_package_clears_repair_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the contract is satisfied, the gate passes and clears any prior repair
    state (so a fixed package unblocks the mission)."""
    _write_partial_paper(tmp_path)
    mpkg.run_gate(tmp_path)  # writes failing state
    assert (tmp_path / "research" / f"{PREFIX}_STATE.json").exists()

    # simulate a now-satisfied contract (real PDFs are out of scope for a unit test)
    monkeypatch.setattr(
        "argus_skill.verticals.physics.manuscript.verify_all_deliverables",
        lambda root: [],
    )
    passed, failures = mpkg.run_gate(tmp_path)
    assert passed is True and failures == []
    assert not (tmp_path / "research" / f"{PREFIX}_STATE.json").exists()
    assert render_active_repair_blocks(tmp_path) == ""


def test_gate_remains_directly_callable_after_stage_registry_removal() -> None:
    """Validators are agent-callable tools, not a hidden stage command registry."""
    from argus_skill.verticals.physics import stages

    assert not hasattr(stages, "STAGE_CHECKS")
    assert callable(mpkg.run_gate)
    assert "manuscript check --layer all" in (mpkg.__doc__ or "")
