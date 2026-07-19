from __future__ import annotations

from pathlib import Path

from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.store import Skill

ROOT = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"


def test_research_vertical_bundles_one_visual_router_and_renderer() -> None:
    texts = dict(iter_vertical_skill_texts("research"))

    skill_text = texts["engineer/research-visualization-router.md"]
    skill = Skill.parse(skill_text)
    assert skill.name == "Research Visualization Router"
    assert skill.protected is True
    assert "FIGURE_PROVENANCE.json" in skill.content
    assert "image-2" in skill.content
    assert "ECharts" in skill.content
    assert "Recharts" in skill.content
    assert "PPT Master" in skill.content
    assert "engineer/research_visual_scripts/browser_render.py" in texts


def test_router_makes_image2_capability_conditional() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    content = Skill.parse(
        texts["engineer/research-visualization-router.md"]
    ).content.lower()

    assert "when configured" in content
    assert "unavailable image route is\nnot a project blocker" in content
    assert "never fake image-2 provenance" in content


def test_results_figures_require_data_derived_claims_and_rendered_review() -> None:
    content = (
        ROOT / "engineer" / "research-results-analysis-and-figures.md"
    ).read_text(encoding="utf-8").lower()

    assert "never hard-code an expected" in content
    assert "counterfactual regression fixture" in content
    assert "ordering of at least one compared method reverses" in content
    assert "render and inspect the actual figure" in content
    assert "not merely lists the output path" in content
