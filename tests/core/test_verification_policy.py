"""What this round must prove, mapped from the vertical's stage.

The bug being fixed: `research_target_level` described project completion but
was injected every round, so a publishable target got seed experiments judged
against publication readiness. These tests pin the separation, and pin the
things that must NOT move with it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.verification_policy import (
    resolve_policy,
)
from argus.roles.prompts.reviewer import render_reviewer_prompt
from argus.verticals._base import load_vertical_contract


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


# -- what actually reaches the roles ---------------------------------------

def test_reviewer_injects_the_resolved_profile_not_just_the_target() -> None:
    """The regression this whole change exists to prevent."""
    from argus.roles.prompts import reviewer as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("verification_instruction = (") :][:1200]

    # Both bars are named, and they are named as different things.
    assert "defines project " in block
    assert "completion, not this round" in block
    assert "policy_line(_policy)" in block
    # The old wording made the project bar the round bar.
    assert "For project-level" not in block


def test_planner_injects_the_resolved_profile_too() -> None:
    from argus.roles.prompts import planner as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("research_target_block = (") :][:1200]

    assert "not this" in block
    assert "_policy.profile" in block


def test_core_has_no_concrete_vertical_profile_registry() -> None:
    import argus.core.verification_policy as policy

    assert not hasattr(policy, "STAGE_PROFILES")
