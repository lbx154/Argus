"""Infrastructure knowledge is a procedure plus a dated project record, never a name.

The skills teach how to survey live sources, stand up pinned candidates and tune
from the official recipe; Argus writes the current answer at project time as a
dated decision record that later projects re-verify.
"""
from __future__ import annotations

import re
from pathlib import Path

import argus
from argus.verticals.research.prompt_policy import (
    _research_learning_block,
    render_role_prompt_context,
    render_role_prompt_fragment,
)

SKILLS_ROOT = Path(argus.__file__).parent / "verticals" / "research" / "skills"
ENGINEER_SKILLS = (
    "engineer/infrastructure-landscape-survey.md",
    "engineer/framework-stand-up-pilot.md",
    "engineer/recipe-anchored-tuning.md",
)
REVIEWER_SKILL = "reviewer/infrastructure-choice-review.md"
GUIDE = "engineer/training-infrastructure-guide.md"


def _text(relative: str) -> str:
    return (SKILLS_ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    return " ".join(_text(relative).lower().split())


def test_procedure_skills_exist_with_frontmatter() -> None:
    for relative in (*ENGINEER_SKILLS, REVIEWER_SKILL, GUIDE):
        text = _text(relative)
        assert text.startswith("---\nname: "), relative
        assert "\ndescription: " in text.split("---", 2)[1], relative


def test_survey_skill_teaches_successor_discovery_and_evidence_cards() -> None:
    survey = _flat(ENGINEER_SKILLS[0])
    assert "successor discovery" in survey
    assert "evidence card" in survey
    assert "argus.tools.web_source" in survey
    assert "semantic-scholar-search.md" in survey
    assert "90" in survey and "180" in survey and "365" in survey
    assert "no material update found" in survey
    assert "exclusion reason" in survey


def test_stand_up_pilot_profiles_a_real_step_and_measures_engine_agreement() -> None:
    pilot = _flat(ENGINEER_SKILLS[1])
    assert "pinned" in pilot
    assert "third_party/<name>/" in pilot
    assert "phase table" in pilot
    assert "log-prob agreement" in pilot
    assert "engine on and off" in pilot
    assert "--intent 'stand-up pilot: <candidate>@<sha>'" in pilot
    assert "gpu-hours per valid update" in pilot


def test_recipe_tuning_changes_one_factor_and_escalates_before_full_runs() -> None:
    tuning = _flat(ENGINEER_SKILLS[2])
    assert "one factor" in tuning
    assert "provenance comment" in tuning
    assert "escalation note" in tuning
    assert "research_notes.md" in tuning
    assert "stop rule" in tuning
    assert "rl-training-collapse-diagnosis.md" in tuning


def test_reviewer_skill_checks_provenance_without_demanding_a_framework() -> None:
    review = _flat(REVIEWER_SKILL)
    assert "re-verify" in review
    assert "pinned" in review
    assert "log-prob agreement" in review
    assert "never demand a particular framework" in review
    assert "never hold because a newer release exists" in review
    assert "never apply this skill to non-training experiments" in review
    assert "unverified" in review


def test_guide_routes_to_the_three_procedure_skills_without_naming_stacks() -> None:
    guide = _text(GUIDE)
    for relative in ENGINEER_SKILLS:
        assert relative in guide, relative
    assert "engineer/rl-training-collapse-diagnosis.md" in guide
    # The guide is a procedure entry point: no dated recommendation, no
    # "examples" list of stacks, no year that would age the advice.
    assert "examples, not required choices" not in guide
    assert re.search(r"\b20\d\d\b", guide) is None


def test_procedure_skills_do_not_recommend_by_remembered_names() -> None:
    for relative in (*ENGINEER_SKILLS, REVIEWER_SKILL, GUIDE):
        text = _text(relative)
        assert "dated hypotheses" in text or "re-verify" in text or "pinned" in text, relative


def test_learning_block_asks_for_a_dated_decision_record() -> None:
    block = _research_learning_block("engineer", "experiment", "execute")
    assert "Surveyed" in block
    assert "History" in block
    assert "engineer/<task-class>-infrastructure-decision.md" in block
    assert "re-verify after" in block
    assert "32 KB" in block
    assert "promotes reviewed project Skills into the shared research layer" in block
    assert len(block.split()) <= 170
    # The Manager variant stays one sentence.
    manager = _research_learning_block("manager", "experiment", "chat")
    assert manager.count(". ") == 0 and manager.rstrip().endswith(".")


def test_round_context_dates_remembered_names_for_research_roles() -> None:
    for role, operation in (("engineer", "execute"), ("reviewer", "evaluate"), ("planner", "plan")):
        for stage in ("idea", "experiment", "paper"):
            context = render_role_prompt_context(
                role=role, operation=operation, stage=stage, scope="", project_root=None,
            )
            assert context.startswith("Today is "), (role, stage)
            assert re.match(r"Today is \d{4}-\d{2}-\d{2}\. ", context), (role, stage)
            assert "dated hypotheses" in context
            fragment = render_role_prompt_fragment(
                role=role, operation=operation, stage=stage, scope="", project_root=None,
            )
            assert "Today is" not in fragment, (role, stage)
    assert "Today is" not in render_role_prompt_context(
        role="engineer", operation="execute", stage="review", scope="", project_root=None,
    )
    assert "Today is" not in render_role_prompt_context(
        role="manager", operation="chat", stage="experiment", scope="", project_root=None,
    )
