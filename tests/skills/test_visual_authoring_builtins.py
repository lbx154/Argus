from __future__ import annotations

import hashlib
from pathlib import Path

import argus_skill.skills.builtins as builtins_module
from argus_skill.skills.builtins import seed_builtin_skills
from argus_skill.skills.store import Skill

ROOT = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"
VISUAL_SKILLS = {
    "engineer/presentation-master.md": (
        "PPT Master (Argus adapter)",
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
    assert "PPT Master (Argus adapter)" in destination.read_text(encoding="utf-8")


def test_presentation_adapter_preserves_user_modified_copy(tmp_path: Path) -> None:
    destination = tmp_path / "engineer" / "presentation-master.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("operator-authored skill\n", encoding="utf-8")

    seeded = seed_builtin_skills(tmp_path)

    assert seeded["engineer/presentation-master.md"] is False
    assert destination.read_text(encoding="utf-8") == "operator-authored skill\n"


def test_presentation_skill_delegates_to_pinned_upstream_toolkit() -> None:
    text = (ROOT / "engineer" / "presentation-master.md").read_text(encoding="utf-8")

    assert "2e29f3d3cfc379c689b07027d0fa776b9ff79291" in text
    assert "${ARGUS_SKILL_BIN:-argus-skill} --install-ppt-master" in text
    assert "update_repo.py" in text
