from __future__ import annotations

from pathlib import Path

import argus_skill
from argus_skill.core.vertical_contract import VerticalLibraryContext
from argus_skill.verticals.research.library_preparation import (
    prepare_skill_libraries,
)
from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment
from argus_skill.verticals.research.stages import STAGE_CHECKLISTS


def _skill(name: str) -> str:
    path = (
        Path(argus_skill.__file__).parent
        / "verticals"
        / "research"
        / "skills"
        / "engineer"
        / name
    )
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def _stage(stage: str) -> str:
    return " ".join(
        item.statement.lower() for item in STAGE_CHECKLISTS[stage]
    )


def test_build_requires_fidelity_positive_control_and_real_baselines() -> None:
    build = _stage("build")

    assert "hypothesis-to-code mapping" in build
    assert "same executed path" in build
    assert "positive control" in build
    assert "strong published baselines" in build
    assert "rename a local heuristic" in build
    assert "public or official benchmarks" in build
    assert "real evaluator" in build


def test_idea_is_source_only_and_never_reselects_after_implementation_failure() -> None:
    idea = _stage("idea")
    discovery = _skill("idea-discovery.md")
    creator = _skill("idea-creator.md")

    assert "candidate execution is forbidden" in idea
    assert "probe experiments are not" in discovery
    assert "no candidate code or experiment" in creator
    assert "do not trigger another selector" in creator


def test_experiments_are_adaptive_and_paper_requires_dominant_wins() -> None:
    experiment = _stage("experiment")

    assert "adaptive programme" in experiment
    assert "no frozen global experiment plan" in experiment
    assert "wins clearly exceed losses" in experiment
    assert "headline and primary comparisons win" in experiment
    assert "strongest same-information baseline is beaten" in experiment
    assert "keep the selected idea and current stage" in experiment


def test_paper_produces_a_complete_draft_before_final_review() -> None:
    paper = _stage("paper")
    router = _skill("venue-paper-skill-router.md")
    venue_format = _skill("venue-format-preflight.md")

    assert "complete paper draft" in paper
    assert "every claim-bearing experiment" in paper
    assert "experiment chronology" in paper
    assert "compile under the selected venue" in paper
    assert "only in review" in paper
    assert "do not run a separate visual gate" in router
    assert "those happen together in review" in router
    assert "selected venue" in venue_format
    assert "academic_language_review" not in venue_format
    assert "paper_layout_review" not in venue_format
    assert "proceed to review for the parallel scientific, visual, and language" in (
        venue_format
    )


def test_review_combines_parallel_scientific_visual_and_language_passes() -> None:
    review = _stage("review")
    final_review = _skill("final-paper-review.md")
    prompt = render_role_prompt_fragment(
        role="reviewer",
        operation="review",
        stage="review",
        scope="final_submission",
        project_root=None,
    ).lower()

    assert "independent of engineer or planner confidence" in review
    assert "executed code" in review
    assert "raw rows" in review
    assert "primary sources" in review
    assert "three independent read-only passes" in review
    assert "inspect every rendered page" in review
    assert "academic prose" in review
    assert "scientific completeness" in final_review
    assert "strict visual quality" in final_review
    assert "academic language" in final_review
    assert "three project review files" in final_review
    assert "recursively crawl old reports or history" in prompt
    assert "never reopen selection or move backward" in prompt


def test_review_loads_the_existing_team_skill(tmp_path: Path) -> None:
    required: list[str] = []
    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="review",
            objective="finish the paper",
            direction="selected",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            required_skill_paths=required,
        )
    )

    assert required == ["engineer/final-paper-review.md"]


def test_research_skills_do_not_reintroduce_parallel_workflow_artifacts() -> None:
    skills_root = (
        Path(argus_skill.__file__).parent
        / "verticals"
        / "research"
        / "skills"
    )
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for role in ("engineer", "reviewer")
        for path in (skills_root / role).glob("*.md")
    )

    for obsolete in (
        "RESEARCH_BRIEF.md",
        "HYPOTHESIS_IMPLEMENTATION_CONTRACT.md",
        "EXPERIMENT_PLAN.md",
        "NOVELTY_CHECK.md",
        "CITATION_AUDIT",
        "LITERATURE_GROUNDING",
        "LIT_MATRIX",
        "RESEARCH_TIMELINE",
        "PAPER_STRUCTURE_BLUEPRINT",
        "FIGURE_PROVENANCE",
        "IMAGE2_FIGURES",
        "REVIEWER_QUESTIONS",
        "PAPER_REVISION_LOG",
        "claims_to_evidence.tsv",
    ):
        assert obsolete not in text
    assert "return to experiments" not in text.lower()
    assert "research/plan" not in text.lower()
