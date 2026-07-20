from __future__ import annotations

import hashlib
from pathlib import Path

import argus_skill.skills.builtins as builtins_module
from argus_skill.skills.builtins import seed_builtin_skills
from argus_skill.skills.store import Skill

ROOT = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"
VISUAL_SKILLS = {
    "engineer/presentation-master.md": (
        "PPT Master for Presentations and Paper Figures (Argus adapter)",
        ("ppt-master", "SKILL.md", "routing.md", "PPT_MASTER_ROOT"),
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


def test_presentation_adapter_upgrades_only_known_unmodified_v1(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "engineer" / "presentation-master.md"
    destination.parent.mkdir(parents=True)
    old = "known unmodified v1\n"
    monkeypatch.setitem(
        builtins_module._SAFE_BUILTIN_UPGRADE_DIGESTS,
        "engineer/presentation-master.md",
        {hashlib.sha256(old.encode()).hexdigest()},
    )
    destination.write_text(old, encoding="utf-8")

    seeded = seed_builtin_skills(tmp_path)

    assert seeded["engineer/presentation-master.md"] is True
    assert (
        "PPT Master for Presentations and Paper Figures (Argus adapter)"
        in destination.read_text(encoding="utf-8")
    )


def test_presentation_adapter_preserves_user_modified_copy(tmp_path: Path) -> None:
    destination = tmp_path / "engineer" / "presentation-master.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("operator-authored skill\n", encoding="utf-8")

    seeded = seed_builtin_skills(tmp_path)

    assert seeded["engineer/presentation-master.md"] is False
    assert destination.read_text(encoding="utf-8") == "operator-authored skill\n"


def test_presentation_skill_delegates_to_pinned_upstream_toolkit() -> None:
    path = ROOT / "engineer" / "presentation-master.md"
    text = path.read_text(encoding="utf-8")
    skill = Skill.parse(text, str(path))

    assert "2e29f3d3cfc379c689b07027d0fa776b9ff79291" in text
    assert "${ARGUS_SKILL_BIN:-argus-skill} --install-ppt-master" in text
    assert "update_repo.py" in text
    assert "research-paper conceptual" in skill.description
    assert "image-2 is unavailable" in skill.description
    assert "does not require a generative image model" in skill.description


def test_figure_spec_summary_requires_router_first() -> None:
    path = ROOT / "engineer" / "figure-spec.md"
    skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))

    assert "Research Visualization Router" in skill.description
    assert "PPT Master" in skill.description
    assert "Do not select FigureSpec directly" in skill.description


def test_visual_summary_previous_versions_are_safe_upgrade_sources() -> None:
    expected = {
        "engineer/presentation-master.md":
            "3b70d2fd3ec0bd00d6a6090238d44b20c4cbcf239b8e2290acdea65c84f47847",
        "engineer/research-results-analysis-and-figures.md":
            "749e2dccdca0fe72b51cf658dfd389c9b47f73a63fb4a512226fbef3d91cba62",
        "engineer/figure-spec.md":
            "3261a0c5f71d318bf212e0b485480503ccb1f30b278b9e07db756f5f2a942398",
    }

    for relative, digest in expected.items():
        assert digest in builtins_module._SAFE_BUILTIN_UPGRADE_DIGESTS[relative]
