from pathlib import Path

from argus.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills,
    seed_vertical_skills,
)
from argus.skills.layered import LayeredSkillStore

STUDIO = "engineer/paper-framework-figure-studio.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_results_figures_keep_claim_checks_agent_owned_and_risk_based() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    content = _flat(texts[STUDIO]).lower()
    assert "never hard-code an expected" in content
    assert "prefer a small counterfactual regression" in content
    assert "reviewer decides" in content
    assert "single scienceplots/matplotlib data-figure path" in content
    assert "any paper data/metric/result chart" in content


def test_agents_receive_visual_library_paths_without_matcher(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    seed_builtin_skills(global_dir, overwrite=True)
    seed_vertical_skills(project_dir, "research", overwrite=True)
    store = LayeredSkillStore(project_dir=project_dir, global_dir=global_dir)

    paths = [path.replace("\\", "/") for path in store.list_paths()]
    assert any(path.endswith(STUDIO) for path in paths)
    assert any(path.endswith("engineer/presentation-master.md") for path in paths)


def test_figure_spec_renderer_is_reachable() -> None:
    """FigureSpec was the third broken route: its documented package path did
    not exist, so the renderer could never be run. Its usage notes now live in
    the script itself and must not point at the old package path."""
    root = Path(__file__).resolve().parents[2]
    renderer = root / "argus/verticals/research/skills/engineer/figure_spec_scripts/figure_renderer.py"
    assert renderer.is_file()
    docstring = renderer.read_text(encoding="utf-8").split('"""')[1]

    assert "argus/builtin_skills/" not in docstring
    assert "figure_spec_scripts" in docstring
    assert "paper-framework-figure-studio.md" in docstring
