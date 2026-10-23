"""What this round must prove, mapped from the vertical's stage.

The bug being fixed: `research_target_level` described project completion but
was injected every round, so a publishable target got seed experiments judged
against publication readiness. These tests pin the separation, and pin the
things that must NOT move with it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.verification_policy import (
    VERIFICATION_PROFILES,
    policy_line,
    profile_for_stage,
    resolve_policy,
)
from argus.roles.prompts.reviewer import render_reviewer_prompt
from argus.verticals._base import load_vertical_contract
from tests.role_rules import rule


def write_state(root: Path, **fields) -> None:
    payload = read_pipeline_state(root)
    payload.update(fields)
    write_pipeline_state(root, payload)


def _profiles(vertical: str) -> dict[str, str]:
    return dict(load_vertical_contract(vertical).verification_stage_profiles or {})


def test_vertical_profile_reaches_reviewer_without_research_target(
    tmp_path: Path,
) -> None:
    """MetaHarness-like verticals must not need a research target merely to
    defer final certification to their last stage.
    """
    write_state(
        tmp_path,
        vertical="argus_maintenance",
        current_stage="change",
    )
    prompt, _ = render_reviewer_prompt(
        SimpleNamespace(skill_store=None),
        objective="improve the harness from feedback",
        operator_messages=[],
        planner_review_instruction="",
        round_index=0,
        session_id=None,
        main_summary="proposal is ready for its first measured run",
        main_error=None,
        working_dir=tmp_path,
        vertical_state_root=tmp_path,
        vertical="argus_maintenance",
    )

    assert "active vertical owns when verification becomes final" in prompt
    assert "`develop` —" in prompt


# -- the fix: early stages stop being judged as submissions ----------------

def test_a_publishable_project_explores_during_the_idea_stage(tmp_path: Path) -> None:
    # Old operator fields in the state file are ignored, never an error.
    write_state(
        tmp_path,
        research_target_level="publishable",
        verification_profile="certify",
        exploration_posture="frontier",
    )

    policy = resolve_policy(stage="idea", stage_profiles=_profiles("research"))

    # The project still aims at publishable, but this round is exploration.
    assert policy.profile == "explore"
    assert policy.resolved


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("idea", "explore"),
        ("experiment", "develop"),
        ("paper", "develop"),
        ("review", "certify"),
    ],
)
def test_research_stage_mapping(stage, expected) -> None:
    assert profile_for_stage(stage, _profiles("research")) == expected


def test_maintenance_stage_mapping_is_vertical_owned() -> None:
    assert profile_for_stage(
        "change",
        _profiles("argus_maintenance"),
    ) == "develop"


# -- what must not move -----------------------------------------------------

def test_a_final_submission_is_always_certified() -> None:
    policy = resolve_policy(
        scope="final_submission", stage="idea", stage_profiles=_profiles("research"),
    )

    assert policy.profile == "certify"


def test_an_unknown_or_missing_stage_is_reported_not_guessed() -> None:
    # Silently picking the strictest reading is the mis-kill; silently picking
    # the loosest would weaken certification. Say it is unresolved.
    assert resolve_policy(stage="not-a-stage").resolved is False
    assert resolve_policy().resolved is False


# -- prompt line ------------------------------------------------------------

def test_policy_line_is_short_enough_for_a_budgeted_prompt() -> None:
    for stage in ("research", "run", "review"):
        line = policy_line(
            resolve_policy(stage=stage, stage_profiles=_profiles("research"))
        )
        assert len(line) < 90, line


def test_policy_line_marks_an_unresolved_profile() -> None:
    assert "(unresolved)" in policy_line(resolve_policy(stage="???"))


# -- what actually reaches the roles ---------------------------------------

def test_reviewer_injects_the_resolved_profile_not_just_the_target() -> None:
    """The regression this whole change exists to prevent."""
    from argus.roles.prompts import reviewer as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("target_block = f") :][:1200]

    assert "policy_line(_policy)" in block
    # Both bars are named, and they are named as different things.
    assert "defines project completion, not this round's bar" in " ".join(
        rule("reviewer", "deciding/SKILL.md").split())


def test_planner_injects_the_resolved_profile_too() -> None:
    from argus.roles.prompts import planner as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("profile = resolve_policy(") :][:600]

    assert ").profile" in block
    assert "this round's verification profile" in block
    assert "not this round's bar" in rule("planner", "stages-and-targets/SKILL.md")


def test_the_injected_block_is_shorter_than_what_it_replaced() -> None:
    """The old block was 386 chars and carried less information."""
    policy = resolve_policy(stage="research", stage_profiles=_profiles("research"))
    block = (
        "Project target `publishable` defines project completion, not this "
        f"round's bar. This round: {policy_line(policy)}. The integrity floor is "
        "identical at every profile. Judge directly and explain in `reason`. If "
        "the direction cannot reach the target, return `replan_requested`.\n\n"
    )

    assert len(block) < 386


def test_vertical_owned_profile_tables_cover_declared_stages() -> None:
    for vertical in ("research", "argus_maintenance"):
        contract = load_vertical_contract(vertical)
        table = dict(contract.verification_stage_profiles or {})
        assert table
        assert set(table) <= set(contract.stage_order)
        assert set(table.values()) <= set(VERIFICATION_PROFILES)


def test_core_has_no_concrete_vertical_profile_registry() -> None:
    import argus.core.verification_policy as policy

    assert not hasattr(policy, "STAGE_PROFILES")
