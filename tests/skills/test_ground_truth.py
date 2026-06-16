"""Tests for the framework-level ground-truth + 实事求是 mandate."""
from __future__ import annotations

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
