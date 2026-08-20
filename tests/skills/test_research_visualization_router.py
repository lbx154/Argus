from pathlib import Path

import yaml

from argus_skill.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills,
    seed_vertical_skills,
)
from argus_skill.skills.layered import LayeredSkillStore

ROOT = (
    Path(__file__).resolve().parents[2]
    / "argus_skill"
    / "verticals"
    / "research"
    / "skills"
)


def _front_and_body(text: str) -> tuple[dict, str]:
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def test_research_vertical_bundles_visual_router_and_renderer() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    front, body = _front_and_body(texts["engineer/research-visualization-router.md"])
    assert set(front) == {"name", "description"}
    assert front["name"] == "Research Visualization Router"
    assert "FIGURE_PROVENANCE.json" in body
    assert "image-2" in body
    assert "ECharts" in body
    assert "Recharts" in body
    assert "PPT Master" in body
    assert "engineer/research_visual_scripts/browser_render.py" in texts


def test_router_makes_image2_capability_conditional() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    _front, body = _front_and_body(texts["engineer/research-visualization-router.md"])
    content = body.lower()
    assert "when configured" in content
    assert "unavailable image route is\nnot a project blocker" in content
    assert "never fake image-2 provenance" in content
    assert "--ppt-master-status" in content
    assert "independent of model api status" in content


def test_router_requires_real_deterministic_figure1_fallback() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    _front, body = _front_and_body(texts["engineer/research-visualization-router.md"])
    content = body.lower()

    assert "figure 1 is a paper deliverable" in content
    assert "ppt master" in content
    assert "deterministic html/svg" in content
    assert "a latex table" in content
    assert "\\includegraphics" in body


def test_results_figures_keep_claim_checks_agent_owned_and_risk_based() -> None:
    text = (ROOT / "engineer" / "research-results-analysis-and-figures.md").read_text(
        encoding="utf-8"
    )
    front, body = _front_and_body(text)
    content = body.lower()
    assert set(front) == {"name", "description"}
    assert "never hard-code an expected" in content
    assert "prefer a small counterfactual regression" in content
    assert "reviewer decides" in content
    for renderer in ("PPT Master", "HTML/SVG", "ECharts", "Recharts", "FigureSpec"):
        assert renderer in front["description"]


def test_agents_receive_visual_library_paths_without_matcher(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    seed_builtin_skills(global_dir, overwrite=True)
    seed_vertical_skills(project_dir, "research", overwrite=True)
    store = LayeredSkillStore(project_dir=project_dir, global_dir=global_dir)

    paths = [path.replace("\\", "/") for path in store.list_paths()]
    assert any(path.endswith("engineer/research-visualization-router.md") for path in paths)
    assert any(path.endswith("engineer/presentation-master.md") for path in paths)
