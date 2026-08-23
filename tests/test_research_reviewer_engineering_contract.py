from __future__ import annotations

from argus_skill.reviewer import Reviewer
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_role_banner


def test_research_reviewer_requires_engineering_audit() -> None:
    banner = vertical_role_banner(load_vertical("research"), "reviewer")

    assert "For experiment claims" in banner
    assert "implementation and raw rows" in banner
    assert "infrastructure or evaluator failure" in banner
    assert "underpowered" in banner
    assert "cannot by themselves trigger replan" in banner
    # The role contract now also carries setup-defect, untrained-baseline,
    # repeated-miss, and retirement-authority rules pinned by dedicated tests.
    # Keep the combined banner bounded without forcing those rules into an
    # unreadable phrase list merely to satisfy the original 800-char budget.
    assert len(banner) < 2_200


def test_research_engineer_receives_only_execution_contract() -> None:
    banner = vertical_role_banner(load_vertical("research"), "engineer")

    assert "Research execution" in banner
    assert "file-disjoint and parallel" in banner
    assert "For experiment claims" not in banner


def test_research_reviewer_prompt_disables_trust_first_shortcut(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    prompt = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="run the frozen premise probe",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="validator exited 0 but the measured target was missed",
        main_error=None,
        prior_checkpoint={},
        working_dir=tmp_path,
    )

    assert "For experiment claims" in prompt
    assert "## Evidence policy" not in prompt
    assert "Trust consistent shown results" not in prompt
    assert "TRUST the scorer, judge the IDEA" not in prompt
