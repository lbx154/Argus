from __future__ import annotations

import re
from pathlib import Path

import argus_skill
from argus_skill.core.vertical_contract import VerticalLibraryContext
from argus_skill.verticals.research.library_preparation import (
    STAGE_PLAYBOOK_PATHS,
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


def _playbook(stage: str) -> str:
    path = (
        Path(argus_skill.__file__).parent
        / "verticals"
        / "research"
        / "skills"
        / STAGE_PLAYBOOK_PATHS[stage]
    )
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def _stage(stage: str) -> str:
    return " ".join(
        item.statement.lower() for item in STAGE_CHECKLISTS[stage]
    )


def test_experiment_requires_fidelity_positive_control_and_real_baselines() -> None:
    experiment = _stage("experiment")

    assert "hypothesis-to-code mapping" in experiment
    assert "same executed path" in experiment
    assert "positive control" in experiment
    assert "strong published baselines" in experiment
    assert "rename a local heuristic" in experiment
    assert "public or official benchmarks" in experiment
    assert "real evaluator" in experiment


def test_idea_is_source_only_and_never_reselects_after_implementation_failure() -> None:
    idea = _stage("idea")
    playbook = _playbook("idea")

    assert "candidate execution is forbidden" in idea
    assert "do not execute candidate code" in playbook
    assert "one selector after all 24 tasks finish" in playbook
    assert "do not reopen the portfolio" in playbook
    assert "operator-locked paper direction" in playbook
    assert "without creating a portfolio" in playbook
    assert "exploratory target" in playbook
    assert "do not manufacture twelve routes" in playbook
    assert "direct idea-only request" in playbook
    assert "do not continue into experiment" in playbook


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
    playbook = _playbook("paper")
    venue_format = _skill("venue-format-preflight.md")

    assert "complete paper draft" in paper
    assert "every claim-bearing experiment" in paper
    assert "experiment chronology" in paper
    assert "compile under the selected venue" in paper
    assert "only in review" in paper
    assert "strong open-access accepted" in playbook
    assert "best paper examples" in playbook
    assert "not a separate scientific, visual" in playbook
    assert "those happen together in review" in playbook
    assert "selected venue" in venue_format
    assert "academic_language_review" not in venue_format
    assert "paper_layout_review" not in venue_format
    assert "proceed to review for the parallel scientific, visual, and language" in (
        venue_format
    )


def test_review_combines_parallel_scientific_visual_and_language_passes() -> None:
    review = _stage("review")
    playbook = _playbook("review")
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
    assert "scientific completeness" in playbook
    assert "strict page-by-page visual quality" in playbook
    assert "academic language" in playbook
    assert "do not create separate scientific" in playbook
    assert "recursively crawl old reports or history" in prompt
    assert "never reopen selection or move backward" in prompt


def test_review_loads_its_single_stage_playbook(tmp_path: Path) -> None:
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

    assert required == ["research-review-playbook.md"]


def test_each_stage_requires_exactly_one_shared_playbook(tmp_path: Path) -> None:
    for stage, playbook in STAGE_PLAYBOOK_PATHS.items():
        required: list[str] = []
        prepare_skill_libraries(
            VerticalLibraryContext(
                workdir=tmp_path / stage,
                state_root=tmp_path / stage,
                stage=stage,
                objective=f"work in {stage}",
                direction="selected",
                workflow_mode="staged",
                paper_mission=True,
                team_task_id="existing-worker",
                runner=None,
                model=None,
                emit=lambda _event: None,
                required_skill_paths=required,
            )
        )
        assert required == [playbook]


def test_every_role_is_directed_to_the_same_stage_playbook() -> None:
    skills_root = (
        Path(argus_skill.__file__).parent
        / "verticals"
        / "research"
        / "skills"
    )
    for stage, playbook in STAGE_PLAYBOOK_PATHS.items():
        for role in ("manager", "planner", "engineer", "reviewer"):
            prompt = render_role_prompt_fragment(
                role=role,
                operation="work",
                stage=stage,
                scope="",
                project_root=None,
            )
            assert f"`{playbook}`" in prompt
            assert str(skills_root / playbook) in prompt
            assert "single workflow playbook" in prompt
            assert "Other Skills are optional tools" in prompt


def test_playbooks_progressively_disclose_existing_specialist_skills() -> None:
    package_root = Path(argus_skill.__file__).parent
    research_skills = package_root / "verticals" / "research" / "skills"
    builtin_skills = package_root / "builtin_skills"

    for stage in STAGE_PLAYBOOK_PATHS:
        text = _playbook(stage)
        assert "progressive disclosure" in text
        assert "do not preload the table" in text
        references = re.findall(
            r"`((?:engineer|reviewer)/[^`]+\.md)`",
            text,
        )
        assert references
        for relative in references:
            assert (
                (research_skills / relative).is_file()
                or (builtin_skills / relative).is_file()
            ), f"{stage} playbook references missing Skill {relative}"


def test_research_skills_do_not_reintroduce_parallel_workflow_artifacts() -> None:
    skills_root = (
        Path(argus_skill.__file__).parent
        / "verticals"
        / "research"
        / "skills"
    )
    paths = [
        *skills_root.glob("*.md"),
        *(skills_root / "engineer").glob("*.md"),
        *(skills_root / "reviewer").glob("*.md"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)

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
    for retired in (
        "auto-research-pipeline.md",
        "idea-discovery.md",
        "idea-creator.md",
        "research-experiment-runner.md",
        "venue-paper-skill-router.md",
        "final-paper-review.md",
        "research-ideation.md",
        "agent-md-new-project-template.md",
        "agent-md-existing-project-optimization-template.md",
    ):
        assert not any(path.name == retired for path in paths)
