"""Two axes: how boldly to explore, and what this round must prove.

The bug being fixed: `research_target_level` described project completion but
was injected every round, so a publishable target got seed experiments judged
against publication readiness. These tests pin the separation, and pin the
things that must NOT move with it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.pipeline_state import (
    primary_pipeline_state_path,
    read_pipeline_state,
    write_pipeline_state,
)
from argus_skill.core.verification_policy import (
    DEFAULT_POSTURE,
    DEFAULT_PROFILE,
    EXPLORATION_POSTURES,
    PROFILE_ORDER,
    VERIFICATION_PROFILES,
    PolicyConfirmationRequired,
    lowers_the_bar,
    normalize_posture,
    normalize_profile,
    policy_line,
    profile_for_stage,
    resolve_policy,
    set_policy,
    stored_policy,
)
from argus_skill.roles.prompts.reviewer import render_reviewer_prompt
from argus_skill.verticals._base import load_vertical_contract


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


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
        vertical="kernel_engineering",
        current_stage="optimize",
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
        vertical="kernel_engineering",
    )

    assert "active vertical owns when verification becomes final" in prompt
    assert "`develop` —" in prompt


# -- defaults ---------------------------------------------------------------

def test_defaults_are_balanced_and_adaptive(project: Path) -> None:
    policy = resolve_policy(
        project,
        stage="research",
        stage_profiles=_profiles("research"),
    )

    assert policy.posture == DEFAULT_POSTURE == "balanced"
    assert policy.configured_profile == DEFAULT_PROFILE == "adaptive"


def test_a_missing_state_file_is_not_an_error(tmp_path: Path) -> None:
    assert stored_policy(tmp_path) == {}
    assert (
        resolve_policy(
            tmp_path,
            stage="research",
            stage_profiles=_profiles("research"),
        ).profile
        == "explore"
    )


def test_a_corrupt_state_file_falls_back_to_defaults(project: Path) -> None:
    path = primary_pipeline_state_path(project)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert (
        resolve_policy(
            project,
            stage="research",
            stage_profiles=_profiles("research"),
        ).posture
        == "balanced"
    )


# -- the fix: early stages stop being judged as submissions ----------------

def test_a_publishable_project_explores_during_the_research_stage(project: Path) -> None:
    write_state(project, research_target_level="publishable")

    policy = resolve_policy(
        project,
        stage="research",
        target_level="publishable",
        stage_profiles=_profiles("research"),
    )

    # The whole point: the project still aims at publishable, but this round
    # is judged as exploration.
    assert policy.target_level == "publishable"
    assert policy.profile == "explore"
    assert policy.source == "stage"


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("research", "explore"), ("plan", "explore"),
        ("benchmark", "develop"), ("run", "develop"),
        ("analysis", "develop"), ("draft", "develop"),
        ("review", "certify"), ("submission", "certify"),
    ],
)
def test_research_stage_mapping(stage, expected) -> None:
    assert profile_for_stage(stage, _profiles("research")) == expected


def test_kernel_stage_mapping_is_vertical_owned() -> None:
    assert profile_for_stage(
        "optimize",
        _profiles("kernel_engineering"),
    ) == "develop"


# -- what must not move -----------------------------------------------------

def test_a_final_submission_is_always_certified(project: Path) -> None:
    # Even with the loosest possible configuration.
    write_state(project, verification_profile="explore", exploration_posture="frontier")

    policy = resolve_policy(project, scope="final_submission", stage="research")

    assert policy.profile == "certify"
    assert policy.source == "final_scope"


def test_final_scope_outranks_an_operator_override(project: Path) -> None:
    write_state(project, verification_profile="explore")

    assert resolve_policy(project, scope="final_submission").profile == "certify"


# -- resolution order -------------------------------------------------------

def test_an_operator_profile_outranks_the_stage_default(project: Path) -> None:
    write_state(project, verification_profile="certify")

    policy = resolve_policy(
        project,
        stage="research",
        stage_profiles=_profiles("research"),
    )

    assert policy.profile == "certify"
    assert policy.source == "operator"


def test_adaptive_defers_to_the_stage(project: Path) -> None:
    write_state(project, verification_profile="adaptive")

    assert (
        resolve_policy(
            project,
            stage="review",
            stage_profiles=_profiles("research"),
        ).source
        == "stage"
    )


def test_an_unknown_stage_is_reported_not_guessed(project: Path) -> None:
    policy = resolve_policy(project, stage="not-a-stage")

    # Silently picking the strictest reading is the mis-kill; silently picking
    # the loosest would weaken certification. Say it is unresolved.
    assert policy.resolved is False
    assert policy.source == "unresolved"
    assert "no profile for stage" in policy.note


def test_a_missing_stage_is_also_unresolved(project: Path) -> None:
    assert resolve_policy(project).resolved is False


# -- lowering the bar needs the operator -----------------------------------

def test_lowering_is_detected_and_raising_is_not() -> None:
    assert lowers_the_bar("certify", "explore") is True
    assert lowers_the_bar("develop", "explore") is True
    assert lowers_the_bar("explore", "certify") is False
    assert lowers_the_bar("develop", "develop") is False


def test_lowering_the_bar_without_confirmation_is_refused(project: Path) -> None:
    write_state(project, verification_profile="certify")

    with pytest.raises(PolicyConfirmationRequired, match="operator"):
        set_policy(project, profile="explore", stage="research")


def test_lowering_the_bar_with_confirmation_applies(project: Path) -> None:
    write_state(project, verification_profile="certify")

    policy = set_policy(project, profile="explore", confirmed=True, stage="research")

    assert policy.profile == "explore"
    assert stored_policy(project)["verification_profile"] == "explore"


def test_raising_the_bar_needs_no_confirmation(project: Path) -> None:
    write_state(project, verification_profile="explore")

    assert set_policy(project, profile="certify", stage="research").profile == "certify"


def test_posture_changes_never_need_confirmation(project: Path) -> None:
    # Posture governs how boldly to explore, not what completion requires.
    policy = set_policy(project, posture="frontier", stage="research")

    assert policy.posture == "frontier"


def test_setting_policy_preserves_other_state_fields(project: Path) -> None:
    write_state(project, research_target_level="publishable", other="keep me")

    set_policy(project, posture="frontier", stage="research")
    payload = read_pipeline_state(project)

    assert payload["research_target_level"] == "publishable"
    assert payload["other"] == "keep me"


def test_setting_policy_creates_the_state_file_when_absent(tmp_path: Path) -> None:
    set_policy(tmp_path, posture="conservative", stage="research")

    assert stored_policy(tmp_path)["exploration_posture"] == "conservative"


@pytest.mark.parametrize("bad", ["lenient", "off", "", None, 3])
def test_unknown_profiles_are_rejected(project: Path, bad) -> None:
    if bad is None:
        return  # None means "leave unchanged", not "invalid"
    with pytest.raises(ValueError, match="verification profile"):
        set_policy(project, profile=bad)


def test_unknown_postures_are_rejected(project: Path) -> None:
    with pytest.raises(ValueError, match="exploration posture"):
        set_policy(project, posture="yolo")


# -- normalization ----------------------------------------------------------

@pytest.mark.parametrize("value", EXPLORATION_POSTURES)
def test_postures_round_trip(value) -> None:
    assert normalize_posture(value.upper()) == value


@pytest.mark.parametrize("value", VERIFICATION_PROFILES + ("adaptive",))
def test_profiles_round_trip(value) -> None:
    assert normalize_profile(f"  {value}  ") == value


def test_garbage_normalizes_to_none() -> None:
    assert normalize_posture("aggressive") is None
    assert normalize_profile("lenient") is None


# -- prompt line ------------------------------------------------------------

def test_policy_line_is_short_enough_for_a_budgeted_prompt(project: Path) -> None:
    for stage in ("research", "run", "review"):
        line = policy_line(
            resolve_policy(
                project,
                stage=stage,
                stage_profiles=_profiles("research"),
            )
        )
        assert len(line) < 90, line


def test_policy_line_marks_an_unresolved_profile(project: Path) -> None:
    assert "(unresolved)" in policy_line(resolve_policy(project, stage="???"))


def test_profile_order_is_strictly_increasing() -> None:
    assert PROFILE_ORDER["explore"] < PROFILE_ORDER["develop"] < PROFILE_ORDER["certify"]


# -- what actually reaches the roles ---------------------------------------

def test_reviewer_injects_the_resolved_profile_not_just_the_target() -> None:
    """The regression this whole change exists to prevent."""
    from argus_skill.roles.prompts import reviewer as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("verification_instruction = (") :][:1200]

    # Both bars are named, and they are named as different things.
    assert "defines project " in block
    assert "completion, not this round" in block
    assert "policy_line(_policy)" in block
    # The old wording made the project bar the round bar.
    assert "For project-level" not in block


def test_planner_injects_the_resolved_profile_too() -> None:
    from argus_skill.roles.prompts import planner as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    block = source[source.index("research_target_block = (") :][:1200]

    assert "not this" in block
    assert "_policy.profile" in block
    assert "_policy.posture" in block


def test_the_injected_block_is_shorter_than_what_it_replaced() -> None:
    """The old block was 386 chars and carried less information."""
    policy = resolve_policy(
        Path("/nonexistent"),
        stage="research",
        stage_profiles=_profiles("research"),
    )
    block = (
        "Project target `publishable` defines project completion, not this "
        f"round's bar. This round: {policy_line(policy)}. The integrity floor is "
        "identical at every profile. Judge directly and explain in `reason`. If "
        "the direction cannot reach the target, return `replan_requested`.\n\n"
    )

    assert len(block) < 386


def test_math_stages_each_resolve_to_a_declared_profile(tmp_path) -> None:
    """Math was absent from ``STAGE_PROFILES`` and fell to the unresolved
    fallback: profile ``develop`` with ``resolved=False``. ``solve`` came out
    right by accident; ``review`` was certifying under a develop-grade policy
    while reporting it had no policy at all."""
    from argus_skill.core.verification_policy import resolve_policy

    expected = {"scope": "explore", "solve": "develop", "review": "certify"}
    for stage, profile in expected.items():
        policy = resolve_policy(
            tmp_path,
            stage=stage,
            vertical="math",
            stage_profiles=_profiles("math"),
        )
        assert policy.resolved, f"math/{stage} still unresolved"
        assert policy.source == "stage"
        assert policy.profile == profile


def test_math_review_stage_requires_the_proof_graph() -> None:
    """The consequence the mapping exists for: ``review`` is the delivery point,
    so a targeted project must have the graph its claim is discharged through."""
    from argus_skill.verticals.math.proof_graph import graph_required_for

    assert graph_required_for("certify", "targeted")
    assert graph_required_for("develop", "targeted")
    assert not graph_required_for("explore", "targeted")
    assert not graph_required_for("certify", "exploratory")


def test_vertical_owned_profile_tables_cover_declared_stages() -> None:
    from argus_skill.core.verification_policy import VERIFICATION_PROFILES

    for vertical in ("research", "math", "kernel_engineering"):
        contract = load_vertical_contract(vertical)
        table = dict(contract.verification_stage_profiles or {})
        assert table
        assert set(table) <= set(contract.stage_order)
        assert set(table.values()) <= set(VERIFICATION_PROFILES)


def test_core_has_no_concrete_vertical_profile_registry() -> None:
    import argus_skill.core.verification_policy as policy

    assert not hasattr(policy, "STAGE_PROFILES")
