"""Tests for the framework-level ground-truth + 实事求是 mandate."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.skills.ground_truth import (
    GROUND_TRUTH_RELPATH,
    ground_truth_mandate,
)

ROLES = ("planner", "engineer", "reviewer")


def test_relpath_constant_is_canonical() -> None:
    assert GROUND_TRUTH_RELPATH == "research/GROUND_TRUTH.md"


@pytest.mark.parametrize("role", ROLES)
def test_mandate_non_empty_for_each_role(role: str) -> None:
    text = ground_truth_mandate(role)
    assert text.strip(), f"mandate empty for role {role!r}"


@pytest.mark.parametrize("role", ROLES)
def test_mandate_contains_key_ideas(role: str) -> None:
    text = ground_truth_mandate(role)
    low = text.lower()
    # Core principle: investigate the real thing yourself.
    assert "investigate" in low
    # 实事求是 / verification language must be present.
    assert "实事求是" in text or "verified" in low or "verify" in low
    # The shared fact-based picture file is named.
    assert GROUND_TRUTH_RELPATH in text
    assert "GROUND_TRUTH.md" in text


@pytest.mark.parametrize("role", ROLES)
def test_mandate_is_general_no_task_specifics(role: str) -> None:
    # The mandate must stay task-agnostic: never hardcode a particular
    # benchmark or hardware/throughput notion.
    low = ground_truth_mandate(role).lower()
    assert "nanochat" not in low
    assert "mfu" not in low
    assert "ssh" not in low
    assert "throughput" not in low


def test_ground_truth_module_source_has_zero_task_specific_terms() -> None:
    # Belt-and-suspenders: the *module source* (mandate + slants + docstring)
    # must carry no task-specific vocabulary, so the primitive stays general.
    import argus_skill.skills.ground_truth as gt

    src = Path(gt.__file__).read_text(encoding="utf-8").lower()
    for term in ("nanochat", "mfu", "ssh", "throughput"):
        assert term not in src, f"task-specific term {term!r} leaked into ground_truth.py"


@pytest.mark.parametrize("role", ROLES)
def test_mandate_makes_ground_truth_a_required_first_gate(role: str) -> None:
    # The fix: GROUND_TRUTH.md is a GATED first-stage deliverable, not a soft
    # "record facts" exhortation. The mandate must convey that structurally.
    text = ground_truth_mandate(role)
    low = text.lower()
    assert GROUND_TRUTH_RELPATH in text
    # Framed as the FIRST deliverable and a GATE.
    assert "first" in low
    assert "gate" in low
    # The binding constraint must be named and backed by MEASURED numbers.
    assert "binding constraint" in low
    assert "measured" in low


def test_speedrun_setup_stage_gates_on_ground_truth() -> None:
    # The speedrun 'setup' stage must mechanically gate on GROUND_TRUTH.md and
    # the reviewer must require a re-verified, MEASURED binding-constraint
    # diagnosis before the mission may advance to 'optimize'.
    from argus_skill.verticals.speedrun.stages import (
        REVIEWER_CHECKLISTS,
        STAGE_CHECKS,
    )

    setup_cmds = [cmd for _label, cmd in STAGE_CHECKS["setup"]]
    assert any("GROUND_TRUTH.md" in cmd for cmd in setup_cmds), setup_cmds
    assert any(
        "research/GROUND_TRUTH.md" in cmd and "test -s" in cmd for cmd in setup_cmds
    ), "setup STAGE_CHECKS must assert research/GROUND_TRUTH.md is non-empty"

    _skill, body, artifacts = REVIEWER_CHECKLISTS["setup"]
    low = body.lower()
    assert "ground_truth.md" in low
    assert "binding" in low and "constraint" in low
    assert "measured" in low
    # Must forbid advancing setup -> optimize without the measured diagnosis.
    assert "optimize" in low
    assert "research/GROUND_TRUTH.md" in artifacts


def test_role_specific_slants_differ() -> None:
    planner = ground_truth_mandate("planner")
    engineer = ground_truth_mandate("engineer")
    reviewer = ground_truth_mandate("reviewer")
    assert "PLANNER SLANT" in planner
    assert "ENGINEER SLANT" in engineer
    assert "REVIEWER SLANT" in reviewer
    # Each role's slant is distinct.
    assert planner != engineer != reviewer != planner


def test_unknown_role_gets_shared_block_without_slant() -> None:
    text = ground_truth_mandate("nobody")
    assert text.strip()
    assert "SLANT" not in text
    # Shared block still present.
    assert "investigate" in text.lower()


def test_default_role_is_shared_block_only() -> None:
    text = ground_truth_mandate()
    assert text.strip()
    assert "SLANT" not in text
