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
    assert "Paper Framework Figure Studio" in body
    assert "engineer/paper-framework-figure-studio.md" in texts
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
    assert "browser-rendered html" in content
    assert "hand-authoring raw svg is not on this table" in content
    assert "a latex table" in content
    assert "\\includegraphics" in body
    studio = texts["engineer/paper-framework-figure-studio.md"]
    assert "S0" in studio and "S7" in studio
    assert "Renderer-neutral design system" in studio
    assert "PPT Master" in studio
    assert "image-2 only when configured" in studio


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


def test_router_points_at_a_renderer_that_exists() -> None:
    """The published route must be runnable: a path the agent cannot resolve is
    why figures got hand-drawn instead."""
    texts = dict(iter_vertical_skill_texts("research"))
    router = texts["engineer/research-visualization-router.md"]

    assert "argus_builtin_skills/" not in router
    assert "browser_render.py" in router

    root = Path(__file__).resolve().parents[2]
    skills = root / "argus_skill/verticals/research/skills/engineer"
    assert (skills / "research_visual_scripts/browser_render.py").is_file()


def test_router_matches_output_format_to_build_route() -> None:
    """`--output *.svg` extracts an existing <svg>, so a CSS layout must be told
    to ask for PDF rather than hand-writing SVG to satisfy the renderer."""
    texts = dict(iter_vertical_skill_texts("research"))
    router = texts["engineer/research-visualization-router.md"].lower()

    assert "--output paper/figures/<id>.pdf" in router
    assert "figure root contains no svg" in router


def test_figure_spec_renderer_is_reachable() -> None:
    """FigureSpec was the third broken route: its documented package path does
    not exist, so the renderer could never be run either."""
    texts = dict(iter_vertical_skill_texts("research"))
    spec = texts["engineer/figure-spec.md"]

    assert "argus_skill/builtin_skills/" not in spec

    root = Path(__file__).resolve().parents[2]
    skills = root / "argus_skill/verticals/research/skills/engineer"
    assert (skills / "figure_spec_scripts/figure_renderer.py").is_file()


def test_figure_one_never_takes_the_flat_route() -> None:
    """A flat-fill renderer draws the boxes the paper's opening figure is judged
    on, so Figure 1 must not qualify for the simple-topology row."""
    router = dict(iter_vertical_skill_texts("research"))[
        "engineer/research-visualization-router.md"
    ].lower()

    assert "a paper's figure 1 never qualifies as the simple row" in router
    assert "simple exact topology in a supporting figure" in router
