from __future__ import annotations

from argus_skill.reviewer import Reviewer
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_role_banner


def test_research_reviewer_requires_engineering_audit() -> None:
    banner = vertical_role_banner(load_vertical("research"), "reviewer")

    assert "overrides generic trust-first review" in banner
    assert "implementation source and raw artifacts" in banner
    assert "idea probes, benchmark preparation, and main experiment runs" in banner
    assert "validator exit code is not sufficient" in banner
    assert "method failure from infrastructure" in banner


def test_research_engineer_does_not_receive_reviewer_contract() -> None:
    assert vertical_role_banner(load_vertical("research"), "engineer") == ""


def test_research_reviewer_prompt_disables_trust_first_shortcut(tmp_path) -> None:
    persist_vertical(tmp_path, "research")
    prompt = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="run the frozen premise probe",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="validator exited 0 with a scientific NO-GO",
        main_error=None,
        prior_checkpoint={},
        working_dir=tmp_path,
    )

    assert "RESEARCH ENGINEERING AUDIT" in prompt
    assert "Shown command/scorer output is only a lead" in prompt
    assert "Trust the engineer by default" not in prompt
    assert "TRUST the scorer, judge the IDEA" not in prompt
