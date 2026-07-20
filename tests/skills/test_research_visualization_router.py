from __future__ import annotations

from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills,
    seed_vertical_skills,
)
from argus_skill.skills.layered import LayeredSkillStore
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
    assert "Prefer PPT Master" in skill.description
    assert "image-2 is not required" in skill.description
    assert "engineer/research_visual_scripts/browser_render.py" in texts


def test_router_makes_image2_capability_conditional() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    content = Skill.parse(
        texts["engineer/research-visualization-router.md"]
    ).content.lower()

    assert "when configured" in content
    assert "unavailable image route is\nnot a project blocker" in content
    assert "never fake image-2 provenance" in content
    assert "--ppt-master-status" in content
    assert "independent of model api status" in content
    assert "dependencies recorded for the\nactive python" in content
    assert "do not default to matplotlib for a non-data conceptual" in content


def test_results_figures_keep_claim_checks_agent_owned_and_risk_based() -> None:
    content = (
        ROOT / "engineer" / "research-results-analysis-and-figures.md"
    ).read_text(encoding="utf-8").lower()

    assert "never hard-code an expected" in content
    assert "prefer a small counterfactual regression" in content
    assert "not a required project artifact or\ncompletion gate" in content
    assert "reviewer decides" in content
    assert "reviewer should inspect the actual rendered figure" in content
    assert "rather than creating a separate mandatory review artifact" in content
    assert "not merely lists the output path" in content

    skill = Skill.parse(
        (ROOT / "engineer" / "research-results-analysis-and-figures.md")
        .read_text(encoding="utf-8")
    )
    for renderer in ("PPT Master", "HTML/SVG", "ECharts", "Recharts", "FigureSpec"):
        assert renderer in skill.description
    assert "Do not default to matplotlib for a non-data conceptual" in skill.content


def test_engineer_matcher_sees_ppt_master_as_a_paper_figure_route(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    seed_builtin_skills(global_dir, overwrite=True)
    seed_vertical_skills(project_dir, "research", overwrite=True)
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    store = LayeredSkillStore(
        project_dir=project_dir,
        global_dir=global_dir,
        runner=backend,
        matcher_model="matcher-model",
    )

    store.find_relevant(
        "Create a polished editable conceptual method figure for a research "
        "paper without a generative image model.",
        role="engineer",
    )

    matcher_prompt = backend.history[0][1]
    assert "Research Visualization Router" in matcher_prompt
    assert "PPT Master for Presentations and Paper Figures" in matcher_prompt
    assert "research-paper conceptual" in matcher_prompt
    assert "image-2 is unavailable" in matcher_prompt
    assert "Do not select FigureSpec directly" in matcher_prompt
