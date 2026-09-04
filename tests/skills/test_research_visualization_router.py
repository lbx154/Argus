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
    assert "image-2" in body
    assert "PPT Master" in body
    assert "Paper Framework Figure Studio" in body
    assert "manifest" not in body.lower()
    assert "hash" not in body.lower()
    assert "engineer/paper-framework-figure-studio.md" in texts
    assert "engineer/research_visual_scripts/browser_render.py" in texts


def test_router_keeps_image2_optional_and_non_semantic() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    _front, body = _front_and_body(texts["engineer/research-visualization-router.md"])
    content = body.lower()
    assert "when configured" in content
    assert "non-claim-bearing" in content
    image2 = texts["engineer/paper-illustration-image2.md"].lower()
    assert "absence never blocks the paper" in image2
    assert "registration files" in image2


def test_router_requires_real_deterministic_figure1_fallback() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    _front, body = _front_and_body(texts["engineer/research-visualization-router.md"])
    content = body.lower()

    assert "figure 1 is a paper deliverable" in content
    assert "ppt master" in content
    assert "browser svg" in content
    assert "boxed\nparagraph or table" in content
    assert "\\includegraphics" in body
    studio = texts["engineer/paper-framework-figure-studio.md"]
    studio_flat = " ".join(studio.split())
    assert "source, target, direction, boundary port" in studio_flat
    assert "connectors terminate at explicit node boundaries" in studio_flat
    assert "no shaft or arrowhead enters an unrelated node" in studio_flat
    assert "final single- or double-column width" in studio_flat
    assert "PPT Master" in studio
    assert (
        "Strict page-by-page visual acceptance happens once, in Review"
        in studio_flat
    )


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


def test_figure_one_prioritizes_exact_topology_over_decorative_richness() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    router = texts[
        "engineer/research-visualization-router.md"
    ].lower()
    normalized = " ".join(router.split())

    assert "exact load-bearing topology" in router
    assert "topology fidelity takes priority over decorative richness" in normalized
    assert "polished figure 1 does not need depth, icons" in normalized
    assert "connector penetration" in router
    assert "figurespec" in router


def test_concept_figures_leave_strict_acceptance_to_review() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    router = texts["engineer/research-visualization-router.md"]
    studio = texts["engineer/paper-framework-figure-studio.md"]

    assert "editable native PPTX through PPT Master" in router
    assert "source and final included export" in router
    assert "not a separate visual gate" in router
    assert "Create only the editable figure source and the final" in studio
    assert "Strict page-by-page visual acceptance happens once, in Review" in studio
    assert "visual-review\nfiles" in studio
