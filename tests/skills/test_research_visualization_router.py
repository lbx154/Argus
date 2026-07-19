from __future__ import annotations

from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.store import Skill


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
