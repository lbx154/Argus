from __future__ import annotations

from pathlib import Path

from argus_skill.skills.builtins import seed_builtin_skills
from argus_skill.skills.store import Skill

ROOT = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"
VISUAL_SKILLS = {
    "engineer/presentation-master.md": (
        "Presentation Master",
        ("editable", "render", "SLIDE_PLAN.json", "pptxgenjs"),
    ),
    "engineer/mermaid-graphviz-diagrams.md": (
        "Mermaid and Graphviz Diagrams",
        ("Mermaid", "Graphviz", "FigureSpec", "source", "render"),
    ),
    "engineer/drawio-diagram-authoring.md": (
        "Draw.io Diagram Authoring",
        (".drawio", "mxGraphModel", "editable", "render"),
    ),
}


def test_visual_authoring_skills_are_valid_and_operational() -> None:
    for relative, (expected_name, required_terms) in VISUAL_SKILLS.items():
        path = ROOT / relative
        skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))

        assert skill.name == expected_name
        assert skill.description
        for term in required_terms:
            assert term in skill.content
        assert "active research vertical" in skill.content


def test_visual_authoring_skills_seed_into_runtime(tmp_path: Path) -> None:
    seeded = seed_builtin_skills(tmp_path)

    for relative in VISUAL_SKILLS:
        assert seeded[relative] is True
        assert (tmp_path / relative).is_file()


def test_presentation_skill_does_not_vendor_restricted_anthropic_skill() -> None:
    text = (ROOT / "engineer" / "presentation-master.md").read_text(encoding="utf-8")

    assert "license forbids reuse" in text
    assert "github.com/anthropics/skills" not in text
