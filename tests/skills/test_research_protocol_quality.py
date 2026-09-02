from __future__ import annotations

from pathlib import Path

import argus_skill
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


def test_paper_is_thesis_driven_with_real_figure_and_table_semantics() -> None:
    paper = _stage("paper")

    assert "thesis-driven" in paper
    assert "experiment chronology" in paper
    assert "figure/table semantics and inclusion" in paper
    assert "tables identify the method and comparison" in paper
    assert "selected venue's current official rules" in paper


def test_review_is_independent_and_follows_direct_claim_evidence() -> None:
    review = _stage("review")
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
    assert "recursively crawl old reports or history" in prompt
    assert "never reopen selection or move backward" in prompt
